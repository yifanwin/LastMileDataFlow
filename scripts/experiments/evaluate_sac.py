"""Evaluate held-out station rollouts and preserve the predeclared trial list."""

from lastmile_dataflow.station_map.rl.devices import isolate_from_argv

isolate_from_argv()  # before any CUDA library loads

from lastmile_dataflow.station_map.rl.training import run_cli

if __name__ == "__main__":
    run_cli("evaluate")
