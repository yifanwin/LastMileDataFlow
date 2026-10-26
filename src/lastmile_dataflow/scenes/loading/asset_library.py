"""Role and dimension selection from the offline asset index."""
from collections import Counter
from dataclasses import dataclass, field
import numpy as np


from .asset_profiles import canonical_category, load_asset_profiles


@dataclass(frozen=True)
class AssetRequest:
    role: str
    size_bounds: dict = field(default_factory=dict)
    support: str | None = None
    room: str | None = None
    excluded_categories: tuple = ()
    available_size_m: tuple | None = None
    yaw_rad: float | None = None
    placement_margin_m: float = .01
    limit: int = 5


class AssetSelector:
    """Select lightweight profiles only; no source scan or geometry compile."""
    def __init__(self, index):
        self.profiles = load_asset_profiles(index)
        self.category_uses, self.asset_uses = Counter(), Counter()

    def select(self, request, rng):
        excluded = {canonical_category(c) for c in request.excluded_categories}
        candidates = []
        for profile in self.profiles:
            if not profile.loadable or not profile.movable or profile.category in excluded or request.role not in profile.roles:
                continue
            if request.support and request.support not in profile.compatible_supports:
                continue
            if request.room and '*' not in profile.compatible_rooms and request.room not in profile.compatible_rooms:
                continue
            if any(not bounds[0] <= getattr(profile, key) <= bounds[1]
                   for key, bounds in request.size_bounds.items()):
                continue
            if request.available_size_m is not None:
                available = np.asarray(request.available_size_m) - 2 * request.placement_margin_m
                yaws = (0., np.pi / 2) if request.yaw_rad is None else (request.yaw_rad,)
                fits = False
                for yaw in yaws:
                    c, s = abs(np.cos(yaw)), abs(np.sin(yaw))
                    extent = [c * profile.width_m + s * profile.depth_m,
                              s * profile.width_m + c * profile.depth_m]
                    fits |= bool(np.all(np.asarray(extent) <= available))
                if not fits:
                    continue
            candidates.append(profile)
        # Prefer unused categories within each shortlist and previously used
        # less often in the task. Random ties remain reproducible by worker seed.
        selected, shortlist_counts = [], Counter()
        while candidates and len(selected) < min(5, request.limit):
            scores = [(self.category_uses[p.category] + shortlist_counts[p.category],
                       self.asset_uses[p.asset_id], rng.random()) for p in candidates]
            index = min(range(len(scores)), key=scores.__getitem__)
            profile = candidates.pop(index)
            selected.append(profile)
            shortlist_counts[profile.category] += 1
        return selected

    def record_use(self, profile):
        self.category_uses[profile.category] += 1
        self.asset_uses[profile.asset_id] += 1
