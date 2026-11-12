"""Spawn target that pins an RL sampler worker to its GPU before CUDA libraries load."""

from lastmile_dataflow.utils.gpu import bind_device


def sampler_entry(physical_gpu, *args):
    """Spawn target: isolate before importing CUDA-dependent worker code."""
    bind_device(int(physical_gpu))
    from .sampling import sample

    sample(*args)
