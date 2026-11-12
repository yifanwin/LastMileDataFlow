"""Measured station rates from trial records; unknown is never converted to failure.

Smoothed maps never take part in selection.
"""

from collections import Counter

# Statuses with a task verdict (the rate denominator), and all statuses that end an episode.
EVALUABLE = frozenset({"success", "failure", "planning_no_solution", "timeout"})
TERMINAL = EVALUABLE | {"initialization_error", "episode_error", "recording_error", "simulation_failure"}


def station_calibration(stations, trials, expected, binding, backend):
    counts = {station["station_id"]: {} for station in stations}
    for row in trials:
        if row.get("status") in EVALUABLE:
            samples = counts[row["station_id"]]
            if row["trial"] in samples:
                raise ValueError("duplicate evaluated trial")
            samples[row["trial"]] = row
    rates = []
    for station in stations:
        samples = counts[station["station_id"]].values()
        rates.append(
            {
                "station_id": station["station_id"],
                "trials": len(samples),
                "successes": sum(row["status"] == "success" for row in samples),
            }
        )
    return {
        **binding,
        "backend": backend,
        "stations": rates,
        "complete": all(row["trials"] == expected for row in rates),
    }


def eligible_stations(stations, calibration, low, high):
    """Only a complete calibration and inclusive thresholds decide; no distance or count limits."""
    if not calibration["complete"]:
        return [], []
    rates = {row["station_id"]: row["successes"] / row["trials"] for row in calibration["stations"]}
    observed = [
        {**station, "success_rate": rates[station["station_id"]]}
        for station in stations
        if station["geometry"] == "valid"
    ]
    return (
        [station for station in observed if station["success_rate"] <= low],
        [station for station in observed if station["success_rate"] >= high],
    )


def completed_episode_rows(rows):
    # Resume/retry records for the same trial must not inflate the denominator.
    completed = {}
    for index, row in enumerate(rows):
        if row.get("status") in TERMINAL:
            key = ("trial", row["trial"]) if "trial" in row else ("row", index)
            completed[key] = row
    return list(completed.values())


def aggregate(stations, trials, expected):
    result = []
    for station in stations:
        rows = [r for r in trials if r["station_id"] == station["station_id"]]
        completed = completed_episode_rows(rows)
        terminal = [r for r in completed if r["status"] in EVALUABLE]
        successes = sum(r["status"] == "success" for r in terminal)
        reasons = Counter(
            ("EpisodeTimeout" if r["status"] == "timeout" else r.get("reason", "unknown"))
            for r in completed
            if r["status"] != "success"
        )
        result.append(
            {
                **station,
                "attempts": [r.get("rollout_id") for r in rows],
                "terminal_trials": len(terminal),
                "successes": successes,
                "completed_episodes": len(completed),
                "episode_errors": len(completed) - len(terminal),
                "timeout_trials": sum(r["status"] == "timeout" for r in terminal),
                "success_rate_scope": "budgeted_evaluable_episodes_including_timeouts_excluding_episode_errors",
                "simulation_failures": sum(r["status"] == "simulation_failure" for r in rows),
                "success_rate": successes / len(terminal) if terminal else None,
                "complete": station["geometry"] == "valid" and len(terminal) == expected,
                "failure_reasons": dict(reasons),
            }
        )
    return result
