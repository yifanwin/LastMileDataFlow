"""Evaluate held-out station rollouts and preserve the predeclared trial list."""
from lastmile_dataflow.workflows.rl_training import run_cli

if __name__ == '__main__':
    run_cli('evaluate')
