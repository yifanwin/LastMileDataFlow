"""Spawn target that pins an RL sampler worker to its GPU before CUDA libraries load."""

from lastmile_dataflow.utils.gpu import bind_device

# Entry points take physical cards. After select_devices(gpus) the main process only sees gpus[0],
# so the Learner, score and evaluate run on in-process device 0 (cuda:0); a Sampler child bound by
# sampler_entry likewise sees only its own card.
VISIBLE_DEVICE = 0


def sampler_entry(physical_gpu, *args):
    """Spawn target: isolate before importing CUDA-dependent worker code."""
    bind_device(int(physical_gpu))
    from .sampling import sample

    sample(*args)
