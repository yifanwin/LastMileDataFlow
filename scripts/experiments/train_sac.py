"""Train the research policy; see reports/rl-station-map for its protocol."""
from lastmile_dataflow.workflows.rl_training import run_cli

if __name__ == '__main__':
    run_cli('train')
