"""Independent construction branches with pending-review/accept/reject lifecycle."""
import copy
from dataclasses import dataclass
from pathlib import Path
import time

import mujoco
import numpy as np

from ..construction.case_schema import CaseTemplate, Review
from ..construction.dsl import ExecutableDSL, SymbolicDSL
from ..io import write_json
from ..scenes.graph import build_scene_graph
from ..scenes.source import instance_catalog
from ..validation.edit import endpoint_checks, physical_checks, rules_pass
from .build_session import migrate_state, model_identity, checkpoint_identity
from .preparation import preparation_guard, settle_scene, SettleConfig
from .simulation import Simulation


@dataclass
class EditTrial:
    sample_id: str
    status: str
    before_graph: object
    after_graph: object = None
    checks: list = None
    reason: str = ''
    settling: dict = None

    def to_dict(self):
        return {'sample_id': self.sample_id, 'status': self.status, 'reason': self.reason,
                'checks': [c.to_dict() for c in self.checks or []], 'settling': self.settling}


class CaseEditSession:
    def __init__(self, prepared, *, settle_config=None, deadline=None, assets=None):
        preparation_guard(prepared.sim)
        self.prepared = prepared
        self.baseline = prepared.sim
        self.sim = self.baseline
        self.spec = self.baseline.spec.copy() if self.baseline.spec is not None else None
        self.config = settle_config or SettleConfig()
        self.deadline = deadline
        self.assets = assets
        self.extra_instances = {}
        self.pending = None
        self.closed = False
        self.revision = prepared.graph.revision

    def active(self):
        if self.closed:
            raise RuntimeError('construction session closed')
        preparation_guard(self.baseline)
        preparation_guard(self.sim)
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError('construction wall-clock budget exhausted')

    def reject(self):
        # Rollback remains possible after deadline, but never after begin().
        preparation_guard(self.baseline)
        preparation_guard(self.sim)
        if self.sim is not self.baseline:
            self.sim.close()
        self.sim = self.baseline
        self.spec = self.baseline.spec.copy() if self.baseline.spec is not None else None
        self.pending = None
        self.extra_instances = {}

    def _branch(self):
        self.reject()
        new = Simulation(self.baseline.model, self.baseline.robot.config, copy.deepcopy(self.baseline.catalog), target=None)
        try:
            new.source = self.baseline.source
            new.restoration = self.baseline.restoration
            new.spec = self.spec
            if hasattr(self.baseline, 'frozen_provenance'):
                new.frozen_provenance = self.baseline.frozen_provenance
            migrate_state(self.baseline, new)
        except BaseException:
            new.close()
            raise
        self.sim = new

    def _ensure_spec(self):
        if self.spec is None:
            if self.sim.source.provenance() != self.sim.frozen_provenance:
                raise ValueError('source changed; cannot structurally rebuild cached baseline')
            self.spec = Simulation.prepare_spec(self.sim.source, self.sim.robot.config)

    def _rebuild(self):
        self.active()
        model = self.spec.compile()
        catalog = instance_catalog(model, self.sim.source)
        for name, asset in self.extra_instances.items():
            catalog = [c for c in catalog if c['instance_id'] != name]
            catalog.append({'instance_id': name, 'mjcf_body': name, 'body_id': model.body(name).id,
                            'asset_id': asset['asset_id'], 'category': asset.get('category'), 'name_map': {}})
        new = Simulation(model, self.sim.robot.config, catalog, target=None)
        try:
            new.source, new.restoration, new.spec = self.sim.source, self.sim.restoration, self.spec
            if hasattr(self.sim, 'frozen_provenance'):
                new.frozen_provenance = self.sim.frozen_provenance
            migrate_state(self.sim, new)
        except BaseException:
            new.close()
            raise
        previous, self.sim = self.sim, new
        previous.close()

    def _pose(self, name, pose):
        self.active()
        b = self.sim.model.body(name).id
        if int(self.sim.model.body_parentid[b]) != 0:
            raise ValueError('nested/articulated root pose edits unsupported')
        count, adr = int(self.sim.model.body_jntnum[b]), int(self.sim.model.body_jntadr[b])
        values = pose['position'] + pose['quaternion_wxyz']
        if count == 1 and self.sim.model.jnt_type[adr] == mujoco.mjtJoint.mjJNT_FREE:
            q, v = int(self.sim.model.jnt_qposadr[adr]), int(self.sim.model.jnt_dofadr[adr])
            self.sim.data.qpos[q:q+7] = values
            self.sim.data.qvel[v:v+6] = 0
            mujoco.mj_forward(self.sim.model, self.sim.data)
        elif count == 0:
            self._ensure_spec()
            body = self.spec.body(name)
            body.pos, body.quat = values[:3], values[3:]
            self._rebuild()
        else:
            raise ValueError('articulated root pose edits unsupported')

    def _apply(self, operation):
        if operation['op'] == 'rotate':
            b = self.sim.model.body(operation['instance']).id
            if not np.allclose(operation['pose']['position'], self.sim.data.xpos[b], atol=1e-8, rtol=0):
                raise ValueError('relative rotate cannot translate the object origin')
        if operation['op'] == 'remove':
            self._ensure_spec()
            name = operation['instance']
            body = self.spec.body(name)
            if body is None or body.parent != self.spec.worldbody:
                raise ValueError('top-level scene instance removal required')
            self.spec.delete(body)
            self.extra_instances.pop(name, None)
            self._rebuild()
            return
        if operation['op'] == 'add':
            self._ensure_spec()
            if self.assets is None:
                raise ValueError('asset catalog unavailable')
            name = operation['instance']
            if self.spec.body(name) is not None:
                raise ValueError('added instance already exists')
            asset_spec, asset = self.assets.load(operation['asset_id'])
            prefix = name + '/'
            self.spec.attach(asset_spec, prefix=prefix, frame=self.spec.worldbody.add_frame())
            self.spec.body(prefix + asset['root_body']).name = name
            self.extra_instances[name] = asset
            self._rebuild()
        self._pose(operation['instance'], operation['pose'])

    def execute(self, template, proposal, executable):
        self.active()
        template = template if isinstance(template, CaseTemplate) else CaseTemplate.from_dict(template)
        proposal = proposal if isinstance(proposal, SymbolicDSL) else SymbolicDSL.from_dict(proposal)
        executable = executable if isinstance(executable, ExecutableDSL) else ExecutableDSL.from_dict(executable)
        if executable.proposal_id != proposal.proposal_id:
            raise ValueError('proposal identity mismatch')
        self.reject()
        before = self.prepared.graph
        checks = proposal.semantic_conflicts(template) + endpoint_checks(template, proposal, before, endpoint='before')
        trial = EditTrial(executable.sample_id, 'binding_not_applicable', before, checks=checks)
        if not rules_pass(checks):
            trial.reason = 'before_invariant_or_binding_failed'
            return trial
        self._branch()
        try:
            for operation in executable.operations:
                self.active()
                name = operation['instance']
                # This is a capability/context check, not an object permission list.
                if operation['op'] != 'add':
                    names = {c['instance_id'] for c in self.sim.catalog}
                    if name not in names or name.startswith(self.sim.robot.config.namespace):
                        raise ValueError('operation requires an ordinary scene instance')
                self._apply(operation)
            affected = {o['instance'] for o in executable.operations}
            stability_instances = None
            if not self.config.require_source_stability:
                # Official source dynamics are observed, not a global gate.
                # Only edited/added/carried bodies must settle. Unedited source
                # neighbours still undergo collision and changed-support checks.
                stability_instances = set(affected)
            settling = settle_scene(self.sim, self.config, deadline=self.deadline,
                                    stability_instances=stability_instances)
            self.revision += 1
            after = build_scene_graph(self.sim, revision=self.revision,
                        stage='settled' if settling['observed_scene_stable'] else 'observed', station=self.prepared.station,
                        config=self.config.graph_config())
            bindings = executable.sampled_parameters.get('bindings', proposal.bindings)
            checks += physical_checks(self.sim, after, settling,
                      affected=affected, config=self.config, before_graph=before)
            checks += endpoint_checks(template, proposal, after, endpoint='after', bindings=bindings)
            trial.after_graph, trial.checks, trial.settling = after, checks, settling
            if not rules_pass(checks):
                trial.status, trial.reason = 'rolled_back', 'after_rules_failed'
                self.reject()
            else:
                trial.status = 'pending_review'
                self.pending = (trial, checkpoint_identity(self.sim, model_identity(self.sim)))
            return trial
        except BaseException as exc:
            self.reject()
            if isinstance(exc, (KeyboardInterrupt, SystemExit, TimeoutError)):
                raise
            trial.status, trial.reason = 'rolled_back', type(exc).__name__ + ':' + str(exc)
            return trial

    def accept(self, review, path):
        self.active()
        review = review if isinstance(review, Review) else Review.from_dict(review)
        if not self.pending or review.sample_id != self.pending[0].sample_id:
            raise ValueError('stale or absent review')
        if review.verdict != 'pass':
            raise ValueError('visual review did not pass')
        trial, identity = self.pending
        if checkpoint_identity(self.sim, model_identity(self.sim)) != identity:
            raise ValueError('candidate changed since rule check')
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        self.sim.freeze(path)
        trial.status = 'accepted'
        return trial

    def close(self):
        if not self.closed:
            # Resource disposal is legal after begin; restoring/resetting is not.
            if self.sim is not self.baseline:
                self.sim.close()
            self.pending = None
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
