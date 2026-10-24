"""One real settling sequence per station, before independent trial creation."""
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np

from .context import clone_sim
from .protocol import SimulationState
from ..control.interface import RobotCommand
from ..outcomes.contacts import simulation_failure
from ..simulation import InitializationError
from ...scenes.stations.initialization import floor_support
from ...scenes.stations.sampling import place_frozen_station, robot_contacts
from ...utils.io import write_json


@dataclass
class PreparedStation:
    state: SimulationState
    path: str
    actual_base: list
    elapsed_s: float


def prepare_station(baseline, station, config, path):
    started = time.monotonic()
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    target = baseline.model.body(baseline.target_id).name
    with closing(clone_sim(baseline, target=target)) as sim:
        place_frozen_station(sim, station)
        sim.begin()

        def check():
            reason = simulation_failure(sim)
            return {'valid': reason is None, 'issues': [reason] if reason else []}

        for _ in range(int(np.ceil(config.initial_settle_s * sim.robot.config.control_hz))):
            _, stop = sim.step(RobotCommand(), substep_check=check)
            if stop:
                raise InitializationError(str(stop['issues']))
        state = SimulationState.capture(sim)
        # Check the measured operation initialization on a new, unstarted view.
        with closing(clone_sim(sim, target=target)) as view:
            base = view.robot.base_pose().tolist()
            if not floor_support(view, base[:2]) or robot_contacts(view):
                raise InitializationError('prepared_station_collision_or_floor')
        sim.save_snapshot(path / 'state.npz')
        elapsed = time.monotonic() - started
        write_json(path / 'preparation.json', {
            'status': 'ready', 'requested_station': station, 'actual_base': base,
            'state': 'state.npz', 'state_spec': state.state_spec,
            'model_dir': getattr(baseline, 'frozen_dir', None),
            'settled_simulation_s': float(sim.data.time - baseline.data.time),
            'elapsed_s': elapsed, 'measured': sim.observe_state(),
        })
    return PreparedStation(state, str(path.resolve()), base, elapsed)
