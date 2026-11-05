"""Write one n6 result into a spheres.yml copy: fixed column and all panda_link0 spheres replaced.

usage: assemble_candidate.py <spheres.yml> <n6 output dir> <out spheres.yml>
Chassis and arm-link spheres are kept byte for byte.
"""
import json
import sys
from pathlib import Path

import yaml

source, result, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
lines = source.read_text().splitlines()
report = json.loads((result / 'robot_part_spheres.json').read_text())
beta = report['beta']
start_link0 = lines.index('  panda_link0:')
start_link1 = lines.index('  panda_link1:')
spheres = yaml.safe_load(source.read_text())['collision_spheres']
# chassis spheres are the flow-style lines after the omron_base comment block; the fixed column follows
first_fixed = next(i for i, line in enumerate(lines) if line.strip() == '- center:')
assert first_fixed < start_link0
fixed = (result / 'omron_base_fixed_column.yml').read_text().splitlines()
link0 = (result / 'panda_link0.yml').read_text().splitlines()
parts = report['parts']
fixed_note = [
    f"  # Fixed column (cylinder R=0.12, omron_base z 0.30-0.70; only z >= chassis top is exposed):",
    f"  # annular-sector cover (scripts/experiments/n6_column_spheres.py), {len(fixed)} spheres,",
    f"  # side/top overhang with buffer {parts['fixed_column']['new']['side_overhang_m']*100:.1f}/"
    f"{parts['fixed_column']['new']['end_overhang_m']*100:.1f} cm (former 8 spheres: 14.8/6.2 cm)."]
link0_note = [
    f"  # Arm base mesh hull: block cover, {parts['link0']['new']['spheres']} spheres, overhang "
    f"{parts['link0']['new']['max_distance_from_solid_m']*100:.1f} cm (the former single sphere left 6 cm uncovered).",
    f"  # Lift column (cylinder R=0.1125, link0 z -0.40-0; z < -0.34 stays inside the fixed column over the",
    f"  # torso travel): {parts['lift_column']['new']['spheres']} spheres, side/top overhang "
    f"{parts['lift_column']['new']['side_overhang_m']*100:.1f}/{parts['lift_column']['new']['end_overhang_m']*100:.1f} cm."]
new = lines[:first_fixed] + fixed_note + fixed + [lines[start_link0]] + link0_note + link0 + lines[start_link1:]
out.write_text('\n'.join(new) + '\n')
loaded = yaml.safe_load(out.read_text())['collision_spheres']
assert loaded['omron_base'][:51] == spheres['omron_base'][:51]
assert all(loaded[k] == spheres[k] for k in spheres if k not in ('omron_base', 'panda_link0'))
print(out, {k: len(v) for k, v in loaded.items()}, 'beta', beta)
