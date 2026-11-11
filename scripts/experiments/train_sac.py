"""Distributed SAC research entry."""
if __name__ == '__main__':
    from lastmile_dataflow.gpu import isolate_training
    isolate_training()
    from lastmile_dataflow.workflows.rl_training import run_cli
    run_cli('train')
