"""Split a column+base candidate: keep only its column spheres or only its arm-base spheres.

usage: split_candidate.py <head spheres.yml> <candidate spheres.yml> <cols|base> <out spheres.yml>
Groups by center height as p2_collision_diagnosis.PARTS: omron_base z >= 0.382 fixed column,
panda_link0 z < 0 lift column, z >= 0 arm base. Other links are taken from head unchanged.
"""
import sys
import yaml

head = yaml.safe_load(open(sys.argv[1]))['collision_spheres']
cand = yaml.safe_load(open(sys.argv[2]))['collision_spheres']
mode, out = sys.argv[3], sys.argv[4]
pick = lambda link, rule: [s for s in link if rule(s['center'][2])]
result = dict(head)
chassis = pick(head['omron_base'], lambda z: z < .382)
if mode == 'cols':
    result['omron_base'] = chassis + pick(cand['omron_base'], lambda z: z >= .382)
    result['panda_link0'] = pick(head['panda_link0'], lambda z: z >= 0) + pick(cand['panda_link0'], lambda z: z < 0)
elif mode == 'base':
    result['panda_link0'] = pick(cand['panda_link0'], lambda z: z >= 0) + pick(head['panda_link0'], lambda z: z < 0)
else:
    raise SystemExit('mode is cols or base')
with open(out, 'w') as f:
    f.write('# experiment copy: ' + mode + ' of ' + sys.argv[2] + '\n')
    yaml.safe_dump({'collision_spheres': result}, f, sort_keys=False, default_flow_style=None)
print(mode, {k: len(v) for k, v in result.items() if k in ('omron_base', 'panda_link0')})
