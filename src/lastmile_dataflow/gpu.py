"""Pin a research process to one GPU; call before torch, warp or mjlab are imported."""
import os
import sys


def isolate_from_argv(argv=None):
    """Hide all other cards, rewriting --gpu to its in-process index 0.

    CUDA reads the visible-device list once, while these libraries load, and
    Warp or torch can then open contexts on device 0 by default. Doing this
    after importing them leaves the run on whichever card happens to be first.
    """
    argv = sys.argv if argv is None else argv
    for position, value in enumerate(argv):
        if value == "--gpu":
            argv[position:position + 2] = ["--gpu", argv[position + 1]]
            index = position + 1
            break
        if value.startswith("--gpu="):
            argv[position:position + 1] = ["--gpu", value.split("=", 1)[1]]
            index = position + 1
            break
    else:
        return None
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    physical = visible.split(",")[int(argv[index])] if visible else argv[index]
    os.environ["CUDA_VISIBLE_DEVICES"] = physical
    argv[index] = "0"
    return physical


def select_devices(gpu_ids, egl_devices=None):
    """Pipeline entries: bind CUDA and EGL to the first requested card before workflow imports.

    Workers spawned for further cards set their own devices from the full list.
    """
    egl = egl_devices[0] if egl_devices else gpu_ids[0]
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]), MUJOCO_EGL_DEVICE_ID=str(egl))
    os.environ.setdefault('MUJOCO_GL', 'egl')


def sampler_entry(physical_gpu, *args):
    """Spawn target: isolate before importing CUDA-dependent worker code."""
    os.environ['CUDA_VISIBLE_DEVICES'] = str(physical_gpu)
    from .workflows.rl_sampling import sample
    sample(*args)


def isolate_training(argv=None):
    argv = sys.argv if argv is None else argv
    for i, value in enumerate(argv):
        if value == '--gpus' or value.startswith('--gpus='):
            requested = argv[i + 1] if value == '--gpus' else value.split('=', 1)[1]
            devices = requested.split(',')
            if not 1 <= len(devices) <= 8 or len(set(devices)) != len(devices):
                raise ValueError('--gpus requires 1–8 distinct devices')
            visible = os.environ.get('CUDA_VISIBLE_DEVICES')
            devices = [visible.split(',')[int(d)] if visible else d for d in devices]
            os.environ['LASTMILE_SAMPLER_GPUS'] = ','.join(devices)
            os.environ['CUDA_VISIBLE_DEVICES'] = devices[0]
            return
    physical = isolate_from_argv(argv)
    if physical is not None:
        os.environ['LASTMILE_SAMPLER_GPUS'] = physical
