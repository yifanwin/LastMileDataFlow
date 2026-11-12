"""Measured obstacle placement on the target support or walkable floor."""

import copy
import itertools
from dataclasses import dataclass, field, replace

import numpy as np

from ..geometry import extract_regions, quat_yaw, rotated_extent, support_rays
from ..stations.initialization import floor_support, inside_triangles, room_triangles, floor_height
from ..loading.assets import AssetRequest

PLACEMENT_GAP_M = 0.003  # clearance between an added obstacle and its support before settling
SURFACE_MARGIN_M = 0.01  # footprint must stay this far inside the support surface


@dataclass
class BuildProposal:
    """Obstacle placements of one edit proposal; each placement is the dict written into the scene record."""

    proposal_id: str
    proposal_intent: str
    placements: list[dict] = field(default_factory=list)

    def runtime_changes(self):
        return {"placements": copy.deepcopy(self.placements)}


def support_regions(sim, task):
    """Select the plane beneath the target, not exposed tops of table legs."""
    candidates = []
    for support in task["support_obstacles"]:
        candidates.extend(extract_regions(sim, support["body"]))
    anchor = np.asarray(task["anchor_world"])
    beneath = []
    for region in candidates:
        local = region.local(anchor.reshape(1, 3))[0]
        a, b, c, d = region.bounds
        if a <= local[0] <= b and c <= local[1] <= d and region.height <= anchor[2] + 0.001:
            beneath.append(region)
    if not beneath:
        return []
    actual = max(beneath, key=lambda r: r.height)
    # Coplanar patches of the same actual support are allowed; lower shelves are not.
    return [r for r in candidates if r.support == actual.support and abs(r.height - actual.height) <= 0.001]


def rotated_placement(local_points, xy, yaw, height, gap):
    """Align footprint CENTER, not a possibly off-center asset root, to the point."""
    c, s = np.cos(yaw), np.sin(yaw)
    rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    rotated = np.asarray(local_points) @ rotation.T
    center = (rotated[:, :2].min(axis=0) + rotated[:, :2].max(axis=0)) / 2
    position = np.r_[np.asarray(xy) - center, height + gap - rotated[:, 2].min()]
    return {"position": position.tolist(), "quaternion_wxyz": quat_yaw(yaw).tolist()}, rotated + position


def footprint_samples(points):
    lo, hi = np.asarray(points)[:, :2].min(axis=0), np.asarray(points)[:, :2].max(axis=0)
    return np.array(list(itertools.product(np.linspace(lo[0], hi[0], 5), np.linspace(lo[1], hi[1], 5))))


def ground_coverage(sim, points):
    triangles = room_triangles(sim)
    samples = footprint_samples(points)
    return all(inside_triangles(xy, triangles) and floor_support(sim, xy) for xy in samples)


def sample_placement(sim, geometry, point, yaw, *, region=None):
    if region is None:
        # Ground plane is measured by a ray; never assume a floor at z=0.
        xy = np.asarray(point["xy"])
        height = floor_height(sim, xy)
        if height is None:
            return None, "no_ground_support"
        pose, points = rotated_placement(geometry["local_points"], xy, yaw, height, PLACEMENT_GAP_M)
        if not ground_coverage(sim, points):
            return None, "full_footprint_outside_ground"
        coverage = False
        kind = "ground"
    else:
        xy = np.asarray(point["xy"])
        pose, points = rotated_placement(geometry["local_points"], xy, yaw, region.height, PLACEMENT_GAP_M)
        if not region.contains(points, -SURFACE_MARGIN_M):
            return None, "full_footprint_outside_surface"
        if not support_rays(sim, 0, region, points):
            return None, "surface_support_rays_failed"
        height, coverage, kind = region.height, True, "surface"
    return {
        "asset_id": geometry["asset_id"],
        "category": geometry["category"],
        "point_id": point["point_id"],
        "point_xy": xy.tolist(),
        "point_sources": point["sources"],
        "pose": pose,
        "yaw_rad": float(yaw),
        "kind": kind,
        "support_height_m": float(height),
        "support_geom": region.geom if region is not None else None,
        "support_instance": region.support if region is not None else None,
        "surface_coverage_verified": coverage,
        "measured_size_m": geometry["size"],
        "world_bounds": {"min": points.min(axis=0).tolist(), "max": points.max(axis=0).tolist()},
    }, None


def role_profiles(ctx, role, rng, *, floor=False):
    available = None
    support = "floor" if floor else None
    if not floor and ctx.geometry.supports:
        largest = max(ctx.geometry.supports, key=lambda r: (r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2]))
        a, b, c, d = largest.bounds
        available = (b - a, d - c)
        support = next(
            (entry["category"] for entry in ctx.baseline.catalog if entry["mjcf_body"] == largest.support), None
        )
    return ctx.selector.select(
        AssetRequest(
            role,
            support=support,
            available_size_m=available,
            excluded_categories=(ctx.task["category"],),
            limit=ctx.settings.asset_shortlist,
        ),
        rng,
    )


def asset_candidates(ctx, profiles, centers, *, floor=False, yaw=0.0):
    """Placement proxies contain measured dimensions, never compile source assets."""
    target_low, target_high = ctx.geometry.target_points.min(axis=0), ctx.geometry.target_points.max(axis=0)
    regions = [None] if floor else ctx.geometry.supports
    for profile in profiles:
        for region in regions:
            if region is not None and profile.support_kind not in ("surface", "floor_or_surface"):
                continue
            for center in centers:
                xy = np.asarray(center[:2], float)
                height = floor_height(ctx.baseline, xy) if region is None else region.height
                if height is None:
                    continue
                half = np.array(rotated_extent(profile.width_m, profile.depth_m, yaw)) / 2
                lo = np.r_[xy - half, height + PLACEMENT_GAP_M]
                hi = np.r_[xy + half, height + PLACEMENT_GAP_M + profile.height_m]
                if np.all(lo < target_high) and np.all(target_low < hi):
                    continue
                if region is not None:
                    corners = np.array(list(itertools.product(*zip(lo, hi))))
                    if not region.contains(corners, -SURFACE_MARGIN_M):
                        continue
                yield {
                    "asset_id": profile.asset_id,
                    "category": profile.category,
                    "kind": "ground" if floor else "surface",
                    "profile_proxy": True,
                    "point_xy": xy.tolist(),
                    "yaw_rad": yaw,
                    "pose": {
                        "position": [*xy, height + PLACEMENT_GAP_M + profile.height_m / 2],
                        "quaternion_wxyz": quat_yaw(yaw).tolist(),
                    },
                    "support_height_m": height,
                    "support_geom": region.geom if region else None,
                    "support_instance": region.support if region else None,
                    "world_bounds": {"min": lo.tolist(), "max": hi.tolist()},
                }


def resolve_assets(ctx, proposal):
    """Resolve measured placement using the shared asset geometry cache."""
    placements = []
    for p in proposal.placements:
        geometry = ctx.assets.geometry(p["asset_id"])
        region = next((r for r in ctx.geometry.supports if r.geom == p["support_geom"]), None)
        placement, reason = sample_placement(
            ctx.baseline,
            geometry,
            {"point_id": proposal.proposal_id, "xy": p["point_xy"], "sources": [{"kind": proposal.proposal_intent}]},
            p["yaw_rad"],
            region=region,
        )
        if placement is None:
            raise ValueError(reason)
        placements.append(placement)
        profile = next(p for p in ctx.selector.profiles if p.asset_id == placement["asset_id"])
        ctx.selector.record_use(profile)
    return replace(proposal, placements=placements)


def propose_obstacles(ctx, rng, attempt):
    settings = ctx.settings
    anchor = np.asarray(ctx.task["anchor_world"][:2])
    placements = []
    counts = ((False, settings.tabletop_count), (True, settings.floor_count))
    for floor, limits in counts:
        count = int(rng.integers(limits[0], limits[1] + 1))
        if count == 0:
            continue
        profiles = role_profiles(ctx, "floor_obstacle" if floor else "arm_obstacle", rng, floor=floor)
        for _ in range(count):
            angles = rng.uniform(-np.pi, np.pi, 32)
            radius = (
                rng.uniform(0.25, ctx.station_config.radius_m, 32)
                if floor
                else settings.tabletop_radius_m * np.sqrt(rng.random(32))
            )
            centers = anchor + np.column_stack((np.cos(angles), np.sin(angles))) * radius[:, None]
            candidates = asset_candidates(ctx, profiles, centers, floor=floor, yaw=float(rng.uniform(-np.pi, np.pi)))
            placement = next(candidates, None)
            if placement is None:
                return None
            placements.append(placement)
    return BuildProposal(f"edit-{attempt:03d}", "edit", placements)
