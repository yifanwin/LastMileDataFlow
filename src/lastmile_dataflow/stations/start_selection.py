"""Inverse selection of reasonable failed starts from successful station regions."""
from collections import Counter
import numpy as np
from ..validation.comparison import validate_station


def start_pairs(spec, station_map):
    """Cheap candidates only: caller MUST run yaw/path/counterfactual comparison."""
    if not station_map: return []
    policy=validate_station(station_map[0])
    for row in station_map: validate_station(row,policy)
    good=[r for r in station_map if r['legal'] and r['plan']=='success']
    edge_successes=Counter(r['edge'] for r in good)
    result=[]
    for s0 in station_map:
        if not all(s0[k] for k in ('legal','visible','facing_target')) or s0['edge_gap_m']>spec.thresholds['edge_gap_max_m'] or s0['plan']!='no_solution': continue
        if spec.case_type in ('case1','case1.5','case1-S') and s0['reach']!='no_solution': continue
        if spec.case_type=='case3' and s0['reach']!='success': continue
        edge=[r for r in station_map if r['edge']==s0['edge'] and r['legal']]
        if spec.case_type in ('case1','case1.5') and any(r['reach']!='no_solution' for r in edge): continue
        for s1 in good:
            move='same_edge' if s0['edge']==s1['edge'] else 'switch_edge'
            if move not in spec.move_types: continue
            if np.linalg.norm(np.array(s0['base'][:2])-s1['base'][:2])<spec.thresholds['translation_min_m']: continue
            if edge_successes[s1['edge']]<spec.thresholds['min_success_stations']: continue
            result.append((s0,s1))
    return sorted(result,key=lambda p:(np.linalg.norm(np.array(p[0]['base'][:2])-p[1]['base'][:2]),p[0]['station_id'],p[1]['station_id']))
