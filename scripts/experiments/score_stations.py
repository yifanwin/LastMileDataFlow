"""Score real station reset observations with a frozen online twin Critic."""
from lastmile_dataflow.workflows.rl_training import run_cli

if __name__ == '__main__':
    run_cli('score')
