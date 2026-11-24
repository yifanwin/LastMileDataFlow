"""Ranking metrics between a station quality score Q and a third-party success rate S (plan 05 §2, plan 04 §5).

Significance is fixed in advance: paired station bootstrap (10,000) of rho_ours - rho_B, 95% interval excluding 0,
cross-checked by Steiger's Z for two dependent correlations sharing S.
"""
from dataclasses import dataclass

import numpy as np
from scipy import stats

BOOTSTRAP = 10_000
POSITIVE_S = 0.5


@dataclass(frozen=True)
class RankMetrics:
    n: int
    spearman: float
    kendall_tau_b: float
    top1: float
    top5: float
    regret: float
    auroc: float | None


def _spearman(q, s):
    return float(stats.spearmanr(q, s).statistic)


def _top_mean(q, s, k):
    """Mean S over the k best-scored stations; ties at the cut share the last slots evenly."""
    order = np.argsort(-q, kind="stable")
    cut = q[order[k - 1]]
    above, tied = s[q > cut], s[q == cut]
    return float((above.sum() + tied.mean() * (k - len(above))) / k)


def auroc(q, s, positive=POSITIVE_S):
    y = s >= positive
    if y.all() or not y.any():
        return None
    ranks = stats.rankdata(q)
    n1 = int(y.sum())
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1)))


def rank_metrics(q, s):
    q, s = np.asarray(q, float), np.asarray(s, float)
    return RankMetrics(
        n=len(q), spearman=_spearman(q, s), kendall_tau_b=float(stats.kendalltau(q, s, variant="b").statistic),
        top1=_top_mean(q, s, 1), top5=_top_mean(q, s, min(5, len(q))), regret=float(s.max() - _top_mean(q, s, 1)),
        auroc=auroc(q, s),
    )


def paired_bootstrap(q_a, q_b, s, *, reps=BOOTSTRAP, seed=0):
    """95% interval of spearman(q_a, s) - spearman(q_b, s), resampling stations jointly."""
    q_a, q_b, s = (np.asarray(v, float) for v in (q_a, q_b, s))
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(reps):
        i = rng.integers(0, len(s), len(s))
        if np.ptp(s[i]) == 0 or np.ptp(q_a[i]) == 0 or np.ptp(q_b[i]) == 0:
            continue
        diffs.append(_spearman(q_a[i], s[i]) - _spearman(q_b[i], s[i]))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"diff": _spearman(q_a, s) - _spearman(q_b, s), "ci95": [float(lo), float(hi)], "valid_reps": len(diffs),
            "significant": bool(lo > 0 or hi < 0)}


def steiger(q_a, q_b, s):
    """Steiger (1980) Z for r(a,s) vs r(b,s) with shared s, on Spearman correlations (Fisher z, pooled r)."""
    r1, r2, r12 = _spearman(q_a, s), _spearman(q_b, s), _spearman(q_a, q_b)
    n = len(s)
    rm = (r1 + r2) / 2
    psi = r12 * (1 - 2 * rm**2) - 0.5 * rm**2 * (1 - 2 * rm**2 - r12**2)
    c = psi / (1 - rm**2) ** 2
    z = (np.arctanh(r1) - np.arctanh(r2)) * np.sqrt((n - 3) / (2 - 2 * c))
    return {"z": float(z), "p_two_sided": float(2 * stats.norm.sf(abs(z))), "r_ab": r12}
