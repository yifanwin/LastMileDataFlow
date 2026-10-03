"""Frozen phase-three configuration, separate from v1 short-action tasks."""
from dataclasses import dataclass, field
from pathlib import Path
import math
from ..config import construct
from ..io import read_json


@dataclass(frozen=True)
class StationConfig:
    schema_version: str = '3.0'
    task_id: str = 'case1-pick'
    case_type: str = 'case1'
    target: str = ''
    grasp_path: str = ''
    grasp_sha256: str = ''
    asset_id: str = ''
    robot_planner_dir: str = ''
    grasp_ids: list = field(default_factory=lambda: [794])
    arms: list = field(default_factory=lambda: ['left', 'right'])
    torso_heights: list = field(default_factory=lambda: [0., .369, .738])
    probes: list = field(default_factory=list)
    radii_m: list = field(default_factory=lambda: [.65, .9, 1.2])
    angle_step_deg: float = 60.
    yaw_offsets_deg: list = field(default_factory=lambda: [0.])
    include_source_base: bool = True
    refine_step_m: float = .10
    max_refinements: int = 4
    max_candidates: int = 40
    max_plans: int = 30
    max_executions: int = 8
    timeout_s: float = 1800.
    attempt_timeout_s: float = 300.
    num_ik_seeds: int = 64
    num_trajopt_seeds: int = 4
    max_attempts: int = 5
    seed: int = 0
    approach_offset_m: float = 0.0
    time_dilation: float = 0.5
    width: int = 640
    height: int = 480
    render_video: bool = True
    # case1.5 per-side station labelling. Labels are roles, never outcome claims: a side's physical
    # result is always read from its own attempts, and a construction-time clearance proxy is never
    # promoted to navigation evidence.
    side_points: dict = field(default_factory=dict)
    side_roles: dict = field(default_factory=dict)
    max_side_assignment_m: float = 1.5
    # One immutable protocol; callers cannot weaken the physical success gate.
    protocol: str = 'strict-pick-v3'

    def __post_init__(self):
        if self.schema_version != '3.0' or self.protocol != 'strict-pick-v3':
            raise ValueError('unknown station schema/protocol')
        if self.case_type not in ('case1', 'case1.5'):
            raise ValueError('only case1 ordinary pick and case1.5 fixed-base side trials are implemented; no handle fallback')
        if self.case_type == 'case1.5' and not self.side_points:
            raise ValueError('case1.5 requires frozen side points from the build evidence')
        for k in ('task_id', 'target', 'grasp_path', 'asset_id', 'robot_planner_dir'):
            if not isinstance(getattr(self,k),str) or not getattr(self,k): raise ValueError(f'missing {k}')
        if len(self.grasp_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.grasp_sha256):
            raise ValueError('grasp digest required')
        if not self.grasp_ids or any(type(x) is not int or x < 0 for x in self.grasp_ids) or len(set(self.grasp_ids)) != len(self.grasp_ids):
            raise ValueError('invalid grasp rows')
        if not self.arms or set(self.arms)-{'left','right'} or len(set(self.arms)) != len(self.arms): raise ValueError('invalid arms')
        for values, lo, hi in ((self.torso_heights,0,.738),(self.radii_m,.1,5.),(self.yaw_offsets_deg,-180,180)):
            if not isinstance(values,list) or any(type(x) not in (float,int) or not math.isfinite(x) or not lo <= x <= hi for x in values): raise ValueError('invalid samples')
        if not self.torso_heights or not self.yaw_offsets_deg: raise ValueError('empty configurations')
        for p in self.probes:
            if not isinstance(p,list) or len(p)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) for x in p): raise ValueError('invalid base probe')
        for k in ('max_candidates','max_plans','max_executions','num_ik_seeds','num_trajopt_seeds','max_attempts','width','height'):
            if type(getattr(self,k)) is not int or getattr(self,k)<=0: raise ValueError(f'invalid {k}')
        for k in ('max_refinements','seed'):
            if type(getattr(self,k)) is not int or getattr(self,k)<0: raise ValueError(f'invalid {k}')
        for k in ('timeout_s','attempt_timeout_s','refine_step_m','time_dilation','angle_step_deg'):
            if type(getattr(self,k)) not in (float,int) or not math.isfinite(getattr(self,k)) or getattr(self,k)<=0: raise ValueError(f'invalid {k}')
        if type(self.approach_offset_m) not in (float,int) or not math.isfinite(self.approach_offset_m) or not 0 <= self.approach_offset_m <= .02: raise ValueError('invalid bounded approach offset')
        if not 10 <= self.angle_step_deg <= 180 or self.time_dilation > 1 or self.width%2 or self.height%2: raise ValueError('invalid sampling/timing/image dimensions')
        if type(self.render_video) is not bool or type(self.include_source_base) is not bool: raise ValueError('invalid flags')
        if not isinstance(self.side_points, dict) or not isinstance(self.side_roles, dict):
            raise ValueError('invalid side assignment')
        for name, point in self.side_points.items():
            if name not in ('side_a', 'side_b', 'side_c') or not isinstance(point, list) or len(point) != 3 \
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in point):
                raise ValueError('invalid side point')
        if set(self.side_roles) - set(self.side_points): raise ValueError('side role without a side point')
        if any(not isinstance(v, str) or not v for v in self.side_roles.values()): raise ValueError('invalid side role label')
        if not math.isfinite(self.max_side_assignment_m) or self.max_side_assignment_m <= 0:
            raise ValueError('invalid side assignment radius')


def load_station_config(path):
    path=Path(path).resolve(); value=read_json(path)
    for k in ('grasp_path','robot_planner_dir'):
        if k in value: value[k]=str((path.parent/value[k]).resolve())
    return construct(StationConfig,value)
