"""Program-only structural transactions; no Agent DSL, review or case template."""
from dataclasses import replace
import copy
from collections import OrderedDict
import time
import mujoco

from .state import migrate_state
from .preparation import preparation_guard, settle_scene
from ..simulation import Simulation
from ...scenes.loading.source import instance_catalog


def clone_preparation(sim):
    preparation_guard(sim)
    target = sim.model.body(sim.target_id).name if sim.target_id is not None else None
    new = Simulation(sim.model, sim.robot.config, copy.deepcopy(sim.catalog), target=target)
    try:
        new.source, new.restoration = sim.source, sim.restoration
        new.spec = sim.spec
        migrate_state(sim, new)
        return new
    except BaseException:
        new.close()
        raise


class ObstacleEditSession:
    """Each candidate gets NEW MjData; reject never restores an executing sim.

    Compiled models are cached by ordered assets, never by candidate poses.
    State migration, including original ctrl and nonrobot qpos, remains by name.
    """
    def __init__(self, baseline, assets, settle_config):
        preparation_guard(baseline)
        self.baseline, self.assets, self.config = baseline, assets, settle_config
        self.models = OrderedDict()
        self.sim = None
        self.closed = False
        self.baseline_spec = None

    def active(self):
        if self.closed:
            raise RuntimeError('obstacle construction session closed')
        preparation_guard(self.baseline)
        if self.sim is not None:
            preparation_guard(self.sim)

    def reject(self):
        self.active()
        if self.sim is not None:
            self.sim.close()
            self.sim = None

    def _model(self, asset_ids):
        key = tuple(asset_ids)
        if key in self.models:
            self.models.move_to_end(key)
            return self.models[key]
        # Multi-object proposals must not retain dozens of complete house models.
        # candidate() already disposed the previous trial, so eviction is safe.
        while len(self.models) >= 4:
            self.models.popitem(last=False)
        if self.baseline_spec is None:
            source_spec = self.baseline.spec
            self.baseline_spec = source_spec.copy() if source_spec is not None else Simulation.prepare_spec(
                self.baseline.source, self.baseline.robot.config)
        spec = self.baseline_spec.copy()
        extra = []
        for i, asset_id in enumerate(asset_ids):
            name = f'procedural_obstacle_{i}'
            if spec.body(name) is not None:
                raise ValueError('obstacle instance name already exists')
            asset_spec, asset = self.assets.load(asset_id)
            if asset['root_motion'] != 'free':
                raise ValueError('procedural obstacles require a free root')
            # THOR receptacle sites are bright debug boxes, not physical geometry.
            # Preserve their identity/pose but put them in a default-hidden site group.
            # Collision geoms, visual meshes and materials are never hidden or changed.
            for site in asset_spec.sites:
                site.group = 5
            prefix = name + '/'
            spec.attach(asset_spec, prefix=prefix, frame=spec.worldbody.add_frame())
            spec.body(prefix + asset['root_body']).name = name
            extra.append({'instance_id': name, 'mjcf_body': name, 'asset_id': asset_id,
                          'category': asset['category'], 'name_map': {}})
        model = spec.compile()
        catalog = instance_catalog(model, self.baseline.source)
        for entry in extra:
            catalog = [c for c in catalog if c['instance_id'] != entry['instance_id']]
            catalog.append({**entry, 'body_id': model.body(entry['mjcf_body']).id})
        self.models[key] = model, catalog, spec
        return self.models[key]

    def candidate(self, placements, *, deadline=None):
        self.reject()
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('variant wall-clock budget exhausted')
        model, catalog, spec = self._model([p['asset_id'] for p in placements])
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('variant wall-clock budget exhausted during model compilation')
        target = self.baseline.model.body(self.baseline.target_id).name if self.baseline.target_id is not None else None
        new = Simulation(model, self.baseline.robot.config, copy.deepcopy(catalog), target=target)
        try:
            new.source, new.restoration, new.spec = self.baseline.source, self.baseline.restoration, spec
            # All added free joints must have legal proposal poses BEFORE forward.
            # Otherwise 6–12 default roots at the origin can overflow contact arena.
            migrate_state(self.baseline, new, forward=False)
            for i, placement in enumerate(placements):
                b = model.body(f'procedural_obstacle_{i}').id
                j = int(model.body_jntadr[b])
                if model.body_jntnum[b] != 1 or model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE:
                    raise ValueError('added obstacle must have one free joint')
                q, v = int(model.jnt_qposadr[j]), int(model.jnt_dofadr[j])
                new.data.qpos[q:q + 7] = placement['pose']['position'] + placement['pose']['quaternion_wxyz']
                new.data.qvel[v:v + 6] = 0.
            mujoco.mj_forward(model, new.data)
        except BaseException:
            new.close()
            raise
        self.sim = new
        return new

    def settle(self, *, deadline=None, duration_s=None):
        self.active()
        if self.sim is None:
            raise RuntimeError('no candidate')
        if duration_s is not None:
            # Continue candidate to the matched control duration, never move bodies manually.
            from dataclasses import replace
            config = replace(self.config, settle_s=duration_s, max_settle_s=duration_s)
        else:
            config = self.config
        names = {c['instance_id'] for c in self.sim.catalog if c['instance_id'].startswith('procedural_obstacle_')}
        return settle_scene(self.sim, config, deadline=deadline, stability_instances=names)

    def close(self):
        if self.sim is not None:
            self.sim.close()
            self.sim = None
        self.models.clear()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def matched_control(baseline, duration_s, settle_config, deadline):
    control = clone_preparation(baseline)
    try:
        config = replace(settle_config, settle_s=duration_s, max_settle_s=duration_s)
        report = settle_scene(control, config, deadline=deadline, stability_instances=set())
        return control, report
    except BaseException:
        control.close()
        raise
