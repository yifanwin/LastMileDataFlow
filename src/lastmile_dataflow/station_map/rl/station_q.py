"""Online twin-Q scores and station-level empirical ranking statistics."""

import numpy as np
import torch
from scipy.stats import spearmanr


@torch.no_grad()
def action_sampled_q(agent, observation, samples=16):
    agent.actor.eval()
    agent.q1.eval()
    agent.q2.eval()
    values = []
    for _ in range(samples):
        action, _ = agent.actor.sample(observation)
        values.append(torch.minimum(agent.q1(observation, action), agent.q2(observation, action)))
    values = torch.stack(values)
    return values.mean(0), values.std(0, correction=0)


def _take(group, candidates, count, selected, used, rng, *, ordered=False):
    candidates = [r for r in candidates if r["station_id"] not in used]
    indices = np.arange(len(candidates)) if ordered else rng.permutation(len(candidates))
    for i in indices[:count]:
        row = candidates[i]
        selected.append({**row, "group": group})
        used.add(row["station_id"])


def select_acceptance_stations(rows, config):
    """Save this selection before observing any rollout outcomes."""
    valid = [r for r in rows if r["valid"]]
    ranked = sorted(valid, key=lambda row: row["q_mean"], reverse=True)
    rng = np.random.default_rng(config["seed"])
    selected, used = [], set()
    _take("top", ranked, config["top"], selected, used, rng, ordered=True)
    third = len(ranked) // 3
    _take("middle", ranked[third : 2 * third], config["middle"], selected, used, rng)
    _take("low", ranked[2 * third :], config["low"], selected, used, rng)
    _take("uniform", ranked, config["uniform"], selected, used, rng)
    # Small sets use every candidate, maintaining disjoint groups.
    requested = sum(config[name] for name in ("top", "middle", "low", "uniform"))
    _take("uniform", ranked, min(requested, len(ranked)) - len(selected), selected, used, rng)
    return selected


def summarize_trials(selection, trials, *, seed):
    rng = np.random.default_rng(seed)
    stations = []
    for row in selection:
        all_rows = [t for t in trials if t["station_id"] == row["station_id"]]
        healthy = [t for t in all_rows if not t["infrastructure_failure"]]
        successes = np.array([t["success"] for t in healthy], dtype=float)
        interval = (
            np.quantile(rng.choice(successes, (1000, len(successes))).mean(-1), [0.025, 0.975]).tolist()
            if len(successes)
            else [None, None]
        )
        stations.append(
            {
                **row,
                "trials": len(all_rows),
                "healthy_trials": len(healthy),
                "success_rate": float(successes.mean()) if len(successes) else None,
                "success_interval": interval,
                "collision_free_success_rate": (
                    float(np.mean([t["success"] and t["collision_free"] for t in healthy])) if healthy else None
                ),
                "return_mean": float(np.mean([t["return"] for t in healthy])) if healthy else None,
            }
        )
    comparable = [s for s in stations if s["success_rate"] is not None]

    def correlation(key):
        if len(comparable) < 2 or len({s["q_mean"] for s in comparable}) < 2 or len({s[key] for s in comparable}) < 2:
            return None
        return float(spearmanr([s["q_mean"] for s in comparable], [s[key] for s in comparable]).statistic)

    groups = {}
    for group in ("top", "middle", "low", "uniform"):
        ids = {s["station_id"] for s in selection if s["group"] == group}
        rows = [t for t in trials if t["station_id"] in ids and not t["infrastructure_failure"]]
        groups[group] = {
            "trials": len(rows),
            "success_rate": float(np.mean([t["success"] for t in rows])) if rows else None,
            "collision_free_success_rate": float(np.mean([t["success"] and t["collision_free"] for t in rows]))
            if rows
            else None,
        }
    return {
        "stations": stations,
        "groups": groups,
        "q_success_spearman": correlation("success_rate"),
        "q_return_spearman": correlation("return_mean"),
        "infrastructure_failures": sum(t["infrastructure_failure"] for t in trials),
    }
