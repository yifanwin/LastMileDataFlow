"""Evaluate held-out station rollouts and preserve the predeclared trial list."""

from lastmile_dataflow.gpu import isolate_from_argv

isolate_from_argv()  # before any CUDA library loads

from lastmile_dataflow.workflows.rl_training import run_cli  # noqa: E402

if __name__ == "__main__":
    run_cli("evaluate")
