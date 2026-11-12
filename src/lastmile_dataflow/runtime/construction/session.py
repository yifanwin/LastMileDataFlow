"""Program-only structural transactions; no Agent DSL, review or case template."""

from collections import OrderedDict
from dataclasses import replace
import copy
import time

import mujoco

from .preparation import preparation_guard, settle_scene
from .state import migrate_state
from ..simulation import closed_on_error
from lastmile_dataflow.runtime.construction.preparation import clone_preparation

MODEL_CACHE_SIZE = 4


OBSTACLE_PREFIX = "procedural_obstacle_"


def obstacle_name(index):
    """Body name of the index-th added obstacle."""
    return f"{OBSTACLE_PREFIX}{index}"


def _set_free_pose(model, data, body_name, pose):
    """Place a free-rooted body at ``pose`` with zero velocity."""
    body = model.body(body_name).id
    joint = int(model.body_jntadr[body])
    if model.body_jntnum[body] != 1 or model.jnt_type[joint] != mujoco.mjtJoint.mjJNT_FREE:
        raise ValueError("added obstacle must have one free joint")
    q, v = int(model.jnt_qposadr[joint]), int(model.jnt_dofadr[joint])
    data.qpos[q : q + 7] = pose["position"] + pose["quaternion_wxyz"]
    data.qvel[v : v + 6] = 0.0


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
            raise RuntimeError("obstacle construction session closed")
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
        while len(self.models) >= MODEL_CACHE_SIZE:
            self.models.popitem(last=False)
        if self.baseline_spec is None:
            self.baseline_spec = self.baseline.source_spec()
        spec = self.baseline_spec.copy()
        extra = []
        for i, asset_id in enumerate(asset_ids):
            name = obstacle_name(i)
            if spec.body(name) is not None:
                raise ValueError("obstacle instance name already exists")
            asset_spec, asset = self.assets.load(asset_id)
            if asset["root_motion"] != "free":
                raise ValueError("procedural obstacles require a free root")
            # THOR receptacle sites are bright debug boxes, not physical geometry.
            # Preserve their identity/pose but put them in a default-hidden site group.
            # Collision geoms, visual meshes and materials are never hidden or changed.
            for site in asset_spec.sites:
                site.group = 5
            prefix = name + "/"
            spec.attach(asset_spec, prefix=prefix, frame=spec.worldbody.add_frame())
            spec.body(prefix + asset["root_body"]).name = name
            extra.append(
                {
                    "instance_id": name,
                    "mjcf_body": name,
                    "asset_id": asset_id,
                    "category": asset["category"],
                    "name_map": {},
                }
            )
        model = spec.compile()
        catalog = [{**copy.deepcopy(c), "body_id": model.body(c["mjcf_body"]).id} for c in self.baseline.catalog]
        for entry in extra:
            catalog = [c for c in catalog if c["instance_id"] != entry["instance_id"]]
            catalog.append({**entry, "body_id": model.body(entry["mjcf_body"]).id})
        self.models[key] = model, catalog, spec
        return self.models[key]

    def candidate(self, placements, *, deadline=None):
        self.reject()
        _check_deadline(deadline, "variant wall-clock budget exhausted")
        if placements:
            model, catalog, spec = self._model([p["asset_id"] for p in placements])
        else:
            model, catalog, spec = self.baseline.model, self.baseline.catalog, self.baseline.spec
        _check_deadline(deadline, "variant wall-clock budget exhausted during model compilation")
        with closed_on_error(self.baseline.derive(model=model, catalog=catalog, spec=spec)) as new:
            # All added free joints must have legal proposal poses BEFORE forward.
            # Otherwise 6–12 default roots at the origin can overflow contact arena.
            migrate_state(self.baseline, new, forward=False)
            for i, placement in enumerate(placements):
                _set_free_pose(model, new.data, obstacle_name(i), placement["pose"])
            mujoco.mj_forward(model, new.data)
        self.sim = new
        return new

    def settle(self, *, deadline=None, duration_s=None):
        self.active()
        if self.sim is None:
            raise RuntimeError("no candidate")
        config = self.config
        if duration_s is not None:
            # Continue candidate to the matched control duration, never move bodies manually.
            config = replace(config, settle_s=duration_s, max_settle_s=duration_s)
        names = {c["instance_id"] for c in self.sim.catalog if c["instance_id"].startswith(OBSTACLE_PREFIX)}
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
    config = replace(settle_config, settle_s=duration_s, max_settle_s=duration_s)
    with closed_on_error(clone_preparation(baseline)) as control:
        report = settle_scene(control, config, deadline=deadline, stability_instances=set())
    return control, report


def _check_deadline(deadline, message):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError(message)
