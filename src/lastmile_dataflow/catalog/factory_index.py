"""Support/target prerequisite records from measured scene graphs, never benchmark tasks."""
from pathlib import Path
import sqlite3
import numpy as np
from ..io import canonical,read_json,digest


def graph_candidates(graph,spec):
    nodes=graph.nodes; records=[]
    supported={edge['args'][0]:edge['args'][1] for edge in graph.edges if edge['predicate']=='supported_by'}
    for name,node in sorted(nodes.items()):
        if node.get('category') not in spec.preconditions['target_categories'] or name not in supported or not node.get('asset_id'): continue
        support=supported[name]
        for region in nodes.values():
            if region.get('kind')!='region' or region.get('support')!=support: continue
            bounds=node.get('collision_bounds_world')
            if bounds is None: continue
            lo,hi=np.array(bounds); corners=np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
            local=(corners-np.array(region['origin']))@np.array(region['axes'])
            a,b,c,d=region['bounds']
            if local[:,0].min()<a-.001 or local[:,0].max()>b+.001 or local[:,1].min()<c-.001 or local[:,1].max()>d+.001 or abs(lo[2]-region['height'])>.008: continue
            if sum(v==support for v in supported.values())<spec.preconditions['min_surface_objects']: continue
            if node.get('root_motion')!='free': continue
            record={'scene_id':graph.scene_id,'target':name,'asset_id':node['asset_id'], 'category':node['category'],
                    'support':support,'region':{k:region[k] for k in ('region_id','support','geom','kind','origin','axes','bounds','height','evidence') if k in region},
                    'support_evidence':'geometry_and_load_bearing_contact','graph_digest':graph.to_dict()['graph_id']}
            record['region']['kind']=region.get('region_kind','plane')
            record['candidate_id']=digest(record); records.append(record)
    return records


def create_geometry_index(records,output):
    output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists(): raise FileExistsError(output)
    with sqlite3.connect(output) as db:
        db.execute('CREATE TABLE candidates(candidate_id TEXT PRIMARY KEY, scene_id TEXT, category TEXT, support TEXT, record TEXT)')
        for record in records:
            db.execute('INSERT INTO candidates VALUES(?,?,?,?,?)',(record['candidate_id'],record['scene_id'],record['category'],record['support'],canonical(record).decode()))
    return output


def query_geometry_index(path,spec,*,limit=100,seed=0):
    if type(limit) is not int or limit<=0: raise ValueError('invalid retrieval limit')
    cats=spec.preconditions['target_categories']; placeholders=','.join('?' for _ in cats)
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True) as db:
        import json
        rows=[json.loads(r[0]) for r in db.execute(f'SELECT record FROM candidates WHERE category IN ({placeholders}) ORDER BY candidate_id',cats)]
    rng=np.random.default_rng(seed); groups={}
    for i in rng.permutation(len(rows)):
        row=rows[int(i)]; groups.setdefault((row['scene_id'],row['category']),[]).append(row)
    result=[]
    while groups and len(result)<limit:
        for key in list(groups):
            result.append(groups[key].pop())
            if not groups[key]: del groups[key]
            if len(result)>=limit: break
    return result
