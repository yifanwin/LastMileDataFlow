"""Entity OBB nodes shared by the control observation and the nominal station-quality input.

Entities come from one static settled snapshot; selection is fixed per nominal station.
Each node is 19D: kind one-hot 4, base-frame centre 3, rotation 6D 6, local half size 3,
centre minus goal anchor 3. Distances from the goal anchor and the base origin to each box
are returned separately for the attention bias and the proximity pooling.
"""

from dataclasses import dataclass

import numpy as np
import torch

NODE_KINDS = ("support", "wall", "tabletop", "ground")
NODE_DIM = 19
MAX_NODES = 20
GROUND_HEIGHT_M = 0.2


@dataclass
class SceneEntities:
    names: list
    kinds: np.ndarray    # (E,) index into NODE_KINDS
    center: np.ndarray   # (E, 3) world
    rotation: np.ndarray  # (E, 3, 3) world, box local axes as columns
    half: np.ndarray     # (E, 3)


@dataclass
class NodeSelection:
    """Per-station padded entity indices; -1 marks padding."""

    index: np.ndarray            # (S, MAX_NODES)
    truncated: np.ndarray        # (S, 3) dropped support/wall, tabletop, ground entities


def _box(points, rotation):
    local = points @ rotation
    lo, hi = local.min(0), local.max(0)
    return rotation @ ((lo + hi) / 2), (hi - lo) / 2


def scene_entities(model, data, *, target_body, support_body, support_height, skip=("robot/",)):
    """One OBB per top-level scene body; the support body splits into its top plate and the rest."""
    import mujoco

    entities = []
    for body in range(1, model.nbody):
        name = model.body(body).name
        if int(model.body_parentid[body]) != 0 or name == target_body or any(s in name for s in skip):
            continue
        bodies = {body} | {b for b in range(body + 1, model.nbody) if int(model.body_rootid[b]) == body}
        geoms = [g for g in range(model.ngeom) if int(model.geom_bodyid[g]) in bodies
                 and (model.geom_contype[g] or model.geom_conaffinity[g])
                 and model.geom_type[g] != mujoco.mjtGeom.mjGEOM_PLANE]
        if not geoms:
            continue
        rotation = data.xmat[body].reshape(3, 3)
        points = {g: _geom_points(model, data, g) for g in geoms}
        if name == support_body:
            plate = [g for g in geoms if points[g][:, 2].max() >= support_height - 0.02]
            rest = [g for g in geoms if g not in plate]
            groups = [("support", plate), ("ground", rest)]
        else:
            stacked = np.concatenate(list(points.values()))
            groups = [("ground" if stacked[:, 2].min() < GROUND_HEIGHT_M else "tabletop", geoms)]
        for kind, members in groups:
            if not members:
                continue
            centre, half = _box(np.concatenate([points[g] for g in members]), rotation)
            entities.append((f"{name}:{kind}", NODE_KINDS.index(kind), centre, rotation, half))
    names, kinds, centres, rotations, halves = zip(*entities)
    return SceneEntities(list(names), np.array(kinds), np.array(centres), np.array(rotations), np.array(halves))


def _geom_points(model, data, g):
    from types import SimpleNamespace
    from lastmile_dataflow.sim.model.geometry import geom_points

    return geom_points(SimpleNamespace(model=model, data=data), g)


def box_distance(points, center, rotation, half):
    """Outside distance from points (..., 3) to boxes broadcast to the same leading shape."""
    u = np.einsum("...ji,...j->...i", rotation, points - center)
    return np.linalg.norm(np.maximum(np.abs(u) - half, 0.0), axis=-1)


def select_nodes(entities, goal_center, stations, base_radius, *, tabletop_radius=0.8, ground_margin=0.7):
    """Quota-limited, stably ordered entity indices for every nominal station (x, y, yaw)."""
    e = entities
    kinds = e.kinds
    plate_ids = np.flatnonzero(np.isin(kinds, (0, 1)))
    plate_order = plate_ids[np.argsort(box_distance(goal_center, e.center[plate_ids], e.rotation[plate_ids],
                                                    e.half[plate_ids]), kind="stable")]
    table_ids = np.flatnonzero(kinds == 2)
    near_goal = box_distance(goal_center, e.center[table_ids], e.rotation[table_ids], e.half[table_ids])
    table_ids = table_ids[near_goal <= tabletop_radius]
    ground_ids = np.flatnonzero(kinds == 3)
    rows, truncated = [], []
    for x, y, _ in stations:
        base = np.array([x, y, goal_center[2]])
        # Tabletop order: distance to the base-target segment, sampled at 11 points.
        segment = base[None] + np.linspace(0, 1, 11)[:, None] * (goal_center - base)[None]
        line = np.min([box_distance(p, e.center[table_ids], e.rotation[table_ids], e.half[table_ids])
                       for p in segment], axis=0) if len(table_ids) else np.zeros(0)
        tabletop = table_ids[np.argsort(line, kind="stable")]
        # Ground: planar distance from the base centre (point at each box's own height) to the box.
        points = np.column_stack((np.full(len(ground_ids), x), np.full(len(ground_ids), y), e.center[ground_ids, 2]))
        planar = box_distance(points, e.center[ground_ids], e.rotation[ground_ids], e.half[ground_ids])
        keep = planar <= base_radius + ground_margin
        ground = ground_ids[keep][np.argsort(planar[keep], kind="stable")]
        chosen = [*plate_order[:6], *tabletop[:10], *ground[:4]]
        truncated.append((max(0, len(plate_order) - 6), max(0, len(tabletop) - 10), max(0, len(ground) - 4)))
        rows.append(chosen + [-1] * (MAX_NODES - len(chosen)))
    return NodeSelection(np.array(rows, dtype=np.int64), np.array(truncated, dtype=np.int64))


class NodeTensors:
    """GPU entity table; computes padded node features in any base frame."""

    def __init__(self, entities, selection, device):
        as_t = lambda x: torch.as_tensor(np.asarray(x), dtype=torch.float32, device=device)
        self.center, self.rotation, self.half = as_t(entities.center), as_t(entities.rotation), as_t(entities.half)
        self.kind = torch.nn.functional.one_hot(torch.as_tensor(entities.kinds, device=device), len(NODE_KINDS)).float()
        self.index = torch.as_tensor(selection.index, device=device)

    def compute(self, station_ids, base_position, base_rotation, goal_center):
        """Nodes (B, 20, 19), mask (B, 20) and distances (B, 2, 20) from goal anchor and base origin."""
        index = self.index[station_ids]
        mask = index >= 0
        safe = index.clamp(min=0)
        local_c = torch.einsum("bji,bnj->bni", base_rotation, self.center[safe] - base_position[:, None])
        local_r = torch.einsum("bji,bnjk->bnik", base_rotation, self.rotation[safe])
        half = self.half[safe]
        goal = torch.einsum("bji,bj->bi", base_rotation, goal_center - base_position)
        anchors = torch.stack((goal, torch.zeros_like(goal)), 1)                      # (B, 2, 3)
        u = torch.einsum("bnji,banj->bani", local_r, anchors[:, :, None, :] - local_c[:, None])  # (B, 2, N, 3)
        distance = (u.abs() - half[:, None]).clamp(min=0).norm(dim=-1)
        nodes = torch.cat((self.kind[safe], local_c, local_r[..., :2].transpose(-1, -2).flatten(-2), half,
                           local_c - goal[:, None]), -1)
        nodes = nodes * mask[..., None]
        distance = distance * mask[:, None]
        return nodes, mask.float(), distance
