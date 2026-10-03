"""Shared, case-agnostic construction layer.

Division of labour, fixed for every case:

* measurement and safety thresholds are program side (:class:`Requirement`, :class:`Frame`);
* semantic trade-offs belong to the Agent, which only ranks/subset/abandons program-enumerated
  candidates and never supplies a number;
* evidence and identity are program side (revisions, digests, local+world values).

Annotation vocabulary:
  strength: geometric_measurement | physical_evidence | geometric_proxy | model_semantic
  layer:    scene_validity | case_intent
Only `scene_validity` requirements enter the physical gate; `case_intent` requirements are the
case construction condition and are kept separate from scene validity.
"""
import functools

import numpy as np

MEASUREMENT = 'geometric_measurement'
PHYSICAL = 'physical_evidence'
PROXY = 'geometric_proxy'
SEMANTIC = 'model_semantic'
SCENE = 'scene_validity'
INTENT = 'case_intent'
STRENGTHS = (MEASUREMENT, PHYSICAL, PROXY, SEMANTIC)
LAYERS = (SCENE, INTENT)
FAILURE_KINDS = ('engineering_failure', 'intent_failure')


@functools.lru_cache(maxsize=256)
def _quat_matrix(quat):
    """Cache the wxyz->matrix conversion: bodies are static inside one build revision."""
    w, x, y, z = (float(v) for v in quat)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def body_rotation(sim, body):
    return _quat_matrix(tuple(np.asarray(sim.data.xquat[body]).round(9)))


def world_to_local(rotation, origin, points):
    return (np.asarray(points) - np.asarray(origin)) @ rotation


def local_to_world(rotation, origin, points):
    return np.asarray(points) @ np.asarray(rotation).T + np.asarray(origin)


class Frame:
    """A reference frame frozen into evidence.

    Every quantity that is relative to a furniture/region is expressed in that object's local
    frame; only robot-facing headings use the world frame. Both values are recorded so the
    orientation meaning cannot drift with camera viewpoint.
    """

    def __init__(self, frame_id, origin, rotation, purpose, source):
        self.frame_id, self.purpose, self.source = frame_id, purpose, source
        self.origin = np.asarray(origin, dtype=float)
        self.rotation = np.asarray(rotation, dtype=float)

    @classmethod
    def of_body(cls, sim, body_id, frame_id, purpose, source='compiled_body_pose'):
        """Gravity-aligned furniture frame: the body's horizontal yaw, with z kept up.

        A compiled THOR body frame can carry a roll/pitch (a table's own frame often has y up). For
        direction semantics a level frame is the honest reference: local x/y lie in the support
        plane and local z is up, so a side vector's vertical component means a height, not a tilt.
        """
        raw = body_rotation(sim, body_id)
        x_axis = raw[:, 0].copy(); x_axis[2] = 0.
        if np.linalg.norm(x_axis) < 1e-9:
            x_axis = raw[:, 1].copy(); x_axis[2] = 0.
        x_axis /= np.linalg.norm(x_axis)
        y_axis = np.cross(np.array([0., 0., 1.]), x_axis)
        rotation = np.column_stack([x_axis, y_axis, np.array([0., 0., 1.])])
        origin = np.asarray(sim.data.xpos[body_id], dtype=float).copy()
        return cls(frame_id, origin, rotation, purpose, source + '_gravity_aligned')

    @classmethod
    def of_support_region(cls, region):
        return cls(region.region_id, region.origin, region.axes, 'support_plane',
                   f'support_region:{region.evidence}')

    def to_dict(self):
        return {'frame_id': self.frame_id, 'origin_world': self.origin.tolist(),
                'axes_world': self.rotation.tolist(), 'purpose': self.purpose,
                'source': self.source, 'convention': 'local_vector = R^T (world_point - origin)'}

    def local(self, points):
        return world_to_local(self.rotation, self.origin, points)

    def world(self, points):
        return local_to_world(self.rotation, self.origin, points)


def make_requirement(name, passed, evidence, *, layer, strength, failure_kind=None, reason=None,
                     required=True, source='program_measurement'):
    if strength not in STRENGTHS: raise ValueError(f'unknown requirement strength: {strength}')
    if layer not in LAYERS: raise ValueError(f'unknown requirement layer: {layer}')
    if failure_kind is not None and failure_kind not in FAILURE_KINDS:
        raise ValueError(f'unknown failure kind: {failure_kind}')
    record = {'requirement': name, 'required': bool(required), 'layer': layer, 'strength': strength,
              'status': 'pass' if passed else 'fail', 'evidence': evidence, 'source': source}
    if not passed and failure_kind is not None:
        record['failure_kind'] = failure_kind
        record['reason'] = reason or name
    return record


def requirement_summary(requirements):
    """Scene validity counts required physical evidence only; intent failures never invalidate a scene."""
    required = [r for r in requirements if r['required']]
    failures = [r for r in required if r['status'] != 'pass']
    engineering = [r for r in failures if r.get('failure_kind') == 'engineering_failure']
    intent = [r for r in failures if r['layer'] == INTENT]
    return {'status': 'pass' if not failures else 'fail', 'total': len(required),
            'failed': len(failures), 'engineering_failures': len(engineering),
            'case_intent_failures': len(intent),
            'failed_requirements': [r['requirement'] for r in failures]}


class CaseConstructor:
    """Per-case construction contract: preflight, generate, local_check, hypotheses.

    A constructor is bound to one live build session. It never mutates the scene: it only
    measures, and returns requirements/candidates for the workflow to act on.
    """

    case_type = 'abstract'

    def __init__(self, session):
        self.session = session
        self.config = session.config
        self.sim = session.sim
        self.restore_region = session.placements[session.config.target]
        self.frozen_initial_base = np.asarray(session.initial_robot_base, dtype=float)

    @property
    def parameters(self): return self.config.parameters

    def build_frames(self, sim): raise NotImplementedError

    def preflight(self, sim): raise NotImplementedError

    def generate(self, sim, frames): raise NotImplementedError

    def local_check(self, sim, frames, requirements):
        raise NotImplementedError

    def pending_hypotheses(self): raise NotImplementedError
