"""Seeded coverage/random/grid sampling, without edit-size costs or local bias."""
import numpy as np


class SamplingRNG:
    """Independent per-dimension Latin strata, replayable across sample indices."""
    def __init__(self, seed, index):
        self.seed, self.dimension = seed, 0
        self.rng = np.random.default_rng(np.random.SeedSequence([seed, index]))

    def random(self):
        return self.rng.random()

    def integers(self, *args, **kwargs):
        return self.rng.integers(*args, **kwargs)

    def coverage_fraction(self, index, count):
        order = np.random.default_rng(np.random.SeedSequence(
            [self.seed, self.dimension, 7341])).permutation(count)
        self.dimension += 1
        return (int(order[index % count]) + self.random())/count


def sample_scalar(value, rng, index=0, count=16, selection='random'):
    if not isinstance(value, list):
        return float(value)
    lo, hi = value
    if selection == 'grid':
        fraction = .5 if count == 1 else (index % count)/(count-1)
    elif selection == 'coverage':
        # A randomized stratum, not a nearest-to-current-pose heuristic.
        fraction = rng.coverage_fraction(index, count) if hasattr(rng, 'coverage_fraction') else ((index % count) + rng.random())/count
    else:
        fraction = rng.random()
    return float(lo+(hi-lo)*fraction)


def sample_xy(bounds, rng, *, mode='uniform', index=0, steps=5, count=16, selection='random'):
    x0, x1, y0, y1 = bounds
    if x0 > x1 or y0 > y1:
        raise ValueError('footprint_exceeds_region')
    if mode == 'grid' or selection == 'grid':
        ix, iy = index % steps, (index//steps) % steps
        fx = .5 if steps == 1 else ix/(steps-1)
        fy = .5 if steps == 1 else iy/(steps-1)
        return [float(x0+(x1-x0)*fx), float(y0+(y1-y0)*fy)]
    if mode not in ('uniform', 'candidate', 'sample_inside'):
        raise ValueError('unknown_sampler')
    # Separate random ordering on y avoids all candidates sitting on a diagonal.
    return [sample_scalar([x0, x1], rng, index, count, selection),
            sample_scalar([y0, y1], rng, index, count, 'random')]
