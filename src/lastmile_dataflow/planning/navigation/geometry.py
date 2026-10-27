"""Held-posture geometry connections and A* routes with explicit base headings."""
import copy
from dataclasses import dataclass
import heapq
import itertools
import math
import time

import mujoco
import numpy as np

from ...runtime.construction.session import clone_preparation
from ...runtime.outcomes.contacts import ContactSampler, NavigationContacts
from ...scenes.stations.initialization import floor_support, room_triangles
from ...scenes.stations.sampling import place_frozen_station


NAVIGATION_PROTOCOL = 'soft_clearance_v1'


@dataclass(frozen=True)
class NavigationSettings:
    target_m: float = .02
    clearance_weight: float = 2.
    max_penetration_m: float = .005
    turn_radius_m: float = .5

    @classmethod
    def from_config(cls, config, reference=None):
        speeds = vars(config) if reference is None else reference
        return cls(config.navigation_margin_m, config.navigation_clearance_weight,
                   config.navigation_max_penetration_m,
                   speeds.get('navigation_speed_m_s', .2) / speeds.get('navigation_yaw_speed_rad_s', .4))


def clearance_penalty(clearance, target):
    return np.clip(1 - np.maximum(clearance, 0.) / target, 0., 1.) ** 2 if target else np.zeros_like(clearance)


def sampled_cost(poses, clearances, settings):
    steps = (np.linalg.norm(np.diff(poses[:, :2], axis=0), axis=1)
             + settings.turn_radius_m * np.abs(np.diff(poses[:, 2])))
    penalty = clearance_penalty(np.asarray(clearances), settings.target_m)
    extra = float(np.sum(steps * (penalty[:-1] + penalty[1:]) / 2) * settings.clearance_weight)
    return float(steps.sum()) + extra, extra


def yaw_gap(target, current):
    return float(np.arctan2(np.sin(target - current), np.cos(target - current)))


def pose_samples(start, goal, translation_step=.01, yaw_step=math.pi / 180):
    start, goal = np.asarray(start, float), np.asarray(goal, float)
    delta = goal - start
    delta[2] = yaw_gap(goal[2], start[2])
    count = max(1, math.ceil(np.linalg.norm(delta[:2]) / translation_step),
                math.ceil(abs(delta[2]) / yaw_step))
    return start + np.linspace(0., 1., count + 1)[:, None] * delta


def pose_path(poses, **details):
    poses = np.asarray(poses, float)
    return {'poses': poses.tolist(), 'xy': poses[:, :2].tolist(),
            'length_m': float(np.linalg.norm(np.diff(poses[:, :2], axis=0), axis=1).sum()),
            'yaw_travel_rad': sum(abs(yaw_gap(b[2], a[2])) for a, b in zip(poses, poses[1:])),
            **details}


@dataclass(frozen=True)
class GeometryCheck:
    valid: bool
    reason: str
    clearance_m: float | None = None
    blocked_pose: tuple | None = None
    cost: float = 0.
    penalty_cost: float = 0.


class GeometryNavigation:
    """Queries use a separate pre-begin MjData; the execution model stays read-only.

    Exact station connections and lazily generated room XY/yaw states share one
    A* objective. The old conservative raster is a display/extent, not a hard gate.
    """
    def __init__(self, baseline, grid, stations, settings=NavigationSettings(), *, budget_s=120.):
        self.sim = clone_preparation(baseline)
        self.grid, self.stations, self.settings = grid, stations, settings
        self.budget_s = budget_s
        self.initial = None
        self.pose_checks, self.edge_checks = {}, {}
        self.patch_checks = {}
        self.floor_checks = {}
        self.contact_sampler = ContactSampler(self.sim)
        self.started_at = time.monotonic()
        self.exhausted = False
        self.floor = room_triangles(self.sim)
        model = self.sim.model
        namespace = self.sim.robot.config.namespace
        self.robot_geoms = np.array([g for g in range(model.ngeom)
            if model.body(int(model.geom_bodyid[g])).name.startswith(namespace)
            and (model.geom_contype[g] or model.geom_conaffinity[g])], dtype=int)
        self.environment = np.array([g for g in range(model.ngeom)
            if not model.body(int(model.geom_bodyid[g])).name.startswith(namespace)
            and (model.geom_contype[g] or model.geom_conaffinity[g])], dtype=int)
        self.pairs = []
        for robot in self.robot_geoms:
            body = model.body(int(model.geom_bodyid[robot])).name.lower()
            allowed_floor = 'base' in body or 'wheel' in body
            environment = [g for g in self.environment
                if ((model.geom_contype[robot] & model.geom_conaffinity[g])
                    or (model.geom_contype[g] & model.geom_conaffinity[robot]))
                and not (allowed_floor and 'floor' in model.geom(g).name.lower())]
            self.pairs.append((int(robot), np.asarray(environment, dtype=int)))

    def close(self):
        self.sim.close()

    def hold(self, station):
        initial = copy.deepcopy(station['initial'])
        field = self.sim.robot.initial_base_field
        previous = None if self.initial is None else {k: v for k, v in self.initial.items() if k != field}
        posture = {k: v for k, v in initial.items() if k != field}
        if previous != posture:
            self.pose_checks.clear()
            self.edge_checks.clear()
            self.patch_checks.clear()
        self.initial = initial
        self.started_at = time.monotonic()
        self.exhausted = False

    def compatible(self, station):
        field = self.sim.robot.initial_base_field
        return {k: v for k, v in self.initial.items() if k != field} == {
            k: v for k, v in station['initial'].items() if k != field}

    @staticmethod
    def _key(pose):
        return tuple(round(float(v), 12) for v in (*pose[:2], yaw_gap(pose[2], 0.)))

    def _supported(self, xy):
        if xy not in self.floor_checks:
            self.floor_checks[xy] = floor_support(self.sim, xy, triangles=self.floor)
        return self.floor_checks[xy]

    def _clearance(self):
        model, data = self.sim.model, self.sim.data
        cutoff = self.settings.target_m + .01
        clearance = cutoff
        for robot, environment in self.pairs:
            distances = np.linalg.norm(data.geom_xpos[environment] - data.geom_xpos[robot], axis=1)
            close = distances - model.geom_rbound[environment] - model.geom_rbound[robot] < cutoff
            close |= model.geom_type[environment] == mujoco.mjtGeom.mjGEOM_PLANE
            for geom in environment[close]:
                clearance = min(clearance, float(mujoco.mj_geomDistance(
                    model, data, robot, int(geom), cutoff, None)))
        return clearance

    def check_pose(self, pose):
        key = self._key(pose)
        if key in self.pose_checks:
            return self.pose_checks[key]
        if time.monotonic() - self.started_at > self.budget_s:
            self.exhausted = True
            return GeometryCheck(False, 'navigation_search_budget_exhausted')
        initial = copy.deepcopy(self.initial)
        initial[self.sim.robot.initial_base_field] = list(key)
        place_frozen_station(self.sim, {'initial': initial})
        contacts = NavigationContacts()
        contacts.observe(self.sim, self.contact_sampler, self.settings.max_penetration_m)
        if not self._supported(key[:2]):
            result = GeometryCheck(False, 'initialization_invalid', blocked_pose=key)
        else:
            clearance = self._clearance()
            severe = contacts.severe_collision or clearance < -self.settings.max_penetration_m
            result = GeometryCheck(not severe, 'navigation_severe_collision' if severe else 'valid',
                                   clearance, key if severe else None)
        self.pose_checks[key] = result
        return result

    def check_edge(self, start, goal):
        key = (self._key(start), self._key(goal))
        if key not in self.edge_checks:
            clearances = []
            samples = pose_samples(key[0], key[1])
            for pose in samples:
                result = self.check_pose(pose)
                if not result.valid:
                    checked = GeometryCheck(False, result.reason, result.clearance_m, result.blocked_pose)
                    if not self.exhausted:
                        self.edge_checks[key] = checked
                        self.edge_checks[(key[1], key[0])] = checked
                    return checked
                clearances.append(result.clearance_m)
            cost, penalty = sampled_cost(samples, clearances, self.settings)
            result = GeometryCheck(True, 'valid', min(clearances), cost=cost, penalty_cost=penalty)
            self.edge_checks[key] = result
            self.edge_checks[(key[1], key[0])] = result
        return self.edge_checks[key]

    def recovery_patch(self, anchor, radius, yaw_tolerance):
        pose = [*anchor['xy'], anchor['yaw']]
        if not self.check_pose(pose).valid:
            return None
        key = (*self._key(pose), radius, yaw_tolerance)
        if key not in self.patch_checks:
            resolution = min(self.grid.resolution, radius)
            half = resolution / 2
            extent = math.floor(radius / resolution)
            boxes = []
            for ix, iy in itertools.product(range(-extent, extent + 1), repeat=2):
                offset = np.array([ix, iy]) * resolution
                if np.linalg.norm(np.abs(offset) + half) > radius + 1e-9:
                    continue
                for yaw_offset in (-yaw_tolerance / 2, yaw_tolerance / 2):
                    lower = [*(np.asarray(anchor['xy']) + offset - half), yaw_offset - yaw_tolerance / 2]
                    upper = [*(np.asarray(anchor['xy']) + offset + half), yaw_offset + yaw_tolerance / 2]
                    corners = [[x, y, anchor['yaw'] + yaw] for x, y, yaw in
                               itertools.product(*zip(lower, upper))]
                    center = [*(np.asarray(anchor['xy']) + offset), anchor['yaw'] + yaw_offset]
                    if (all(self.check_pose(corner).valid for corner in corners)
                            and self.check_edge(pose, center).valid):
                        boxes.append([*lower, *upper])
                    if self.exhausted:
                        return None
            self.patch_checks[key] = boxes
        return {'anchor': anchor, 'cells': [], 'boxes': self.patch_checks[key]}

    def _path(self, route, goal, status):
        checks = [self.edge_checks[(a, b)] for a, b in zip(route, route[1:])]
        clearance = min([self.pose_checks[route[0]].clearance_m, *[c.clearance_m for c in checks]])
        return pose_path(route, goal=goal, reachable_goals=[goal], method='geometry_soft_astar',
                         clearance_lower_bound_m=clearance, route_cost=sum(c.cost for c in checks),
                         clearance_penalty_cost=sum(c.penalty_cost for c in checks),
                         optimization_status=status, navigation_protocol=NAVIGATION_PROTOCOL,
                         goal_reachability_scope='selected_reference_goal')

    def _base_cost(self, start, goal):
        return (float(np.linalg.norm(np.asarray(goal[:2]) - start[:2]))
                + self.settings.turn_radius_m * abs(yaw_gap(goal[2], start[2])))

    def _heuristic(self, pose, goals):
        translation = np.linalg.norm(goals[:, :2] - pose[:2], axis=1)
        delta = goals[:, 2] - pose[2]
        return float(np.min(translation + self.settings.turn_radius_m * np.abs(np.arctan2(np.sin(delta), np.cos(delta)))))

    def _room_neighbors(self, pose):
        cell = self.grid.cell(pose[:2])
        if cell is None:
            return
        y, x = cell
        for dy, dx in itertools.product((-1, 0, 1), repeat=2):
            yy, xx = y + dy, x + dx
            if 0 <= yy < self.grid.free.shape[0] and 0 <= xx < self.grid.free.shape[1]:
                yield self._key([*self.grid.xy((yy, xx)), pose[2]])
        step = math.pi / 12
        heading = pose[2] / step
        headings = (round(heading) - 1, round(heading) + 1) if abs(heading - round(heading)) < 1e-9 else (math.floor(heading), math.ceil(heading))
        for index in headings:
            yield self._key([*pose[:2], index * step])

    def _neighbors(self, pose, exact):
        candidates = set(self._room_neighbors(pose))
        for target in exact:
            if pose in exact or np.linalg.norm(np.asarray(target[:2]) - pose[:2]) <= 3 * self.grid.resolution:
                candidates.add(target)
        candidates.discard(pose)
        return candidates

    def _search(self, start, goal_nodes, neighbors, seed=None):
        goals = np.asarray(list(goal_nodes))
        distances, parent = {start: 0.}, {}
        best_goal = start if start in goal_nodes else None
        best_cost = 0. if best_goal is not None else float('inf')
        if seed is not None:
            route = [self._key(pose) for pose in seed['poses']]
            for source, target in zip(route, route[1:]):
                distances[target] = distances[source] + self.edge_checks[(source, target)].cost
                parent[target] = source
            best_goal, best_cost = route[-1], distances[route[-1]]
        queue = [(cost + self._heuristic(node, goals), cost, node) for node, cost in distances.items()]
        heapq.heapify(queue)
        expanded = 0
        while queue and not self.exhausted:
            bound, cost, node = heapq.heappop(queue)
            if bound >= best_cost - 1e-10:
                break
            if cost > distances[node] + 1e-10:
                continue
            expanded += 1
            candidates = [(cost + self._base_cost(node, target) + self._heuristic(target, goals), target)
                          for target in neighbors(node)]
            for lower_bound, target in sorted(candidates):
                if lower_bound >= best_cost - 1e-10:
                    break
                if cost + self._base_cost(node, target) >= distances.get(target, float('inf')) - 1e-10:
                    continue
                checked = self.check_edge(node, target)
                if self.exhausted:
                    break
                if not checked.valid:
                    continue
                value = cost + checked.cost
                if value >= distances.get(target, float('inf')) - 1e-10:
                    continue
                distances[target], parent[target] = value, node
                heapq.heappush(queue, (value + self._heuristic(target, goals), value, target))
                if target in goal_nodes and value < best_cost:
                    best_cost, best_goal = value, target
        if best_goal is None:
            return None, expanded
        route = [best_goal]
        while route[-1] in parent:
            route.append(parent[route[-1]])
        status = 'budget_limited' if self.exhausted else 'optimal_in_graph'
        return self._path(list(reversed(route)), goal_nodes[best_goal], status), expanded

    def plan(self, start, goals):
        """Search exact connections and room XY/yaw states by one soft objective."""
        start = self._key(start)
        source = self.check_pose(start)
        cell = self.grid.cell(start[:2])
        diagnostics = {'conservative_start_blocked': cell is None or not bool(self.grid.free[cell])}
        if not source.valid:
            return {'path': None, 'reason': source.reason, **diagnostics}
        checked_goals = [(goal, self.check_pose([*goal['xy'], goal['yaw']])) for goal in goals]
        goal_nodes = {self._key([*goal['xy'], goal['yaw']]): goal for goal, checked in checked_goals if checked.valid}
        if not goal_nodes:
            reason = checked_goals[0][1].reason if len(checked_goals) == 1 else 'recovery_region_empty'
            return {'path': None, 'reason': 'navigation_search_budget_exhausted' if self.exhausted else reason, **diagnostics}
        exact = {start, *goal_nodes, *(self._key(s['base']) for s in self.stations)}
        # The same A* first supplies a finite-graph upper bound, then optimizes
        # it with room states. Geometry/cost queries are cached across both.
        seed, seed_expanded = self._search(start, goal_nodes, lambda node: exact - {node})
        path, expanded = self._search(start, goal_nodes, lambda node: self._neighbors(node, exact), seed)
        reason = 'valid' if path is not None else 'navigation_search_budget_exhausted' if self.exhausted else 'navigation_search_exhausted'
        return {'path': path, 'reason': reason, 'expanded_nodes': expanded + seed_expanded, **diagnostics}
