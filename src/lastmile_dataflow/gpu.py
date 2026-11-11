"""Pin a research process to one GPU; call before torch, warp or mjlab are imported."""
import os
import ctypes
import json
import subprocess
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
    select_devices([int(physical)])
    argv[index] = "0"
    return physical


def _query_egl_devices():
    """Query NVIDIA's CUDA ordinals without creating a display or GPU context."""
    egl = ctypes.CDLL('libEGL.so.1')
    egl.eglGetProcAddress.restype = ctypes.c_void_p
    query_devices = ctypes.CFUNCTYPE(
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_int))(egl.eglGetProcAddress(b'eglQueryDevicesEXT'))
    query_attribute = ctypes.CFUNCTYPE(
        ctypes.c_uint, ctypes.c_void_p, ctypes.c_int,
        ctypes.POINTER(ctypes.c_ssize_t))(egl.eglGetProcAddress(b'eglQueryDeviceAttribEXT'))
    count = ctypes.c_int()
    if not query_devices(0, None, ctypes.byref(count)):
        raise RuntimeError('cannot enumerate EGL devices; provide --egl-devices explicitly')
    devices = (ctypes.c_void_p * count.value)()
    query_devices(count.value, devices, ctypes.byref(count))
    result = {}
    for index, device in enumerate(devices):
        ordinal = ctypes.c_ssize_t()
        # EGL_CUDA_DEVICE_NV: CUDA ordinal (queried with all devices visible).
        if query_attribute(device, 0x323A, ctypes.byref(ordinal)):
            result[ordinal.value] = index
    return result


def egl_device_map():
    # EGL_CUDA_DEVICE_NV follows CUDA visibility too. Query in a clean process,
    # before any CUDA library can cache a narrowed device list.
    environment = dict(os.environ, CUDA_DEVICE_ORDER='PCI_BUS_ID')
    environment.pop('CUDA_VISIBLE_DEVICES', None)
    result = subprocess.run(
        [sys.executable, '-c', 'import json; from lastmile_dataflow.gpu import '
         '_query_egl_devices; print(json.dumps(_query_egl_devices()))'],
        env=environment, capture_output=True, text=True, check=True)
    return {int(gpu): device for gpu, device in json.loads(result.stdout).items()}


def resolve_egl_devices(gpu_ids, egl_devices=None):
    if egl_devices is not None:
        if len(egl_devices) != len(gpu_ids):
            raise ValueError('provide one EGL device index per GPU')
        return list(egl_devices)
    if os.environ.get('MUJOCO_GL') == 'disable':
        return [None] * len(gpu_ids)
    mapping = json.loads(os.environ.get('LASTMILE_EGL_DEVICES', '{}'))
    if any(str(gpu) not in mapping for gpu in gpu_ids):
        mapping.update({str(gpu): device for gpu, device in egl_device_map().items()})
    return [mapping[str(gpu)] for gpu in gpu_ids]


def bind_device(gpu, egl_device=None):
    """Bind a worker before its first renderer; CPU workers may then hide CUDA."""
    device = resolve_egl_devices([gpu], None if egl_device is None else [egl_device])[0]
    os.environ.update(CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=str(gpu),
                      LASTMILE_RENDER_GPU=str(gpu))
    if device is not None:
        os.environ['MUJOCO_EGL_DEVICE_ID'] = str(device)
    os.environ.setdefault('MUJOCO_GL', 'egl')


def select_devices(gpu_ids, egl_devices=None):
    """Resolve once in the parent and inherit the mapping in every spawned worker."""
    devices = resolve_egl_devices(gpu_ids, egl_devices)
    os.environ['LASTMILE_EGL_DEVICES'] = json.dumps(dict(zip(map(str, gpu_ids), devices)))
    bind_device(gpu_ids[0], devices[0])
    return devices


def bind_cpu_renderer():
    """CPU preparation/execution still render on the parent's selected physical GPU."""
    gpu = os.environ.get('LASTMILE_RENDER_GPU')
    if gpu is not None:
        bind_device(int(gpu))
    os.environ['CUDA_VISIBLE_DEVICES'] = ''


def sampler_entry(physical_gpu, *args):
    """Spawn target: isolate before importing CUDA-dependent worker code."""
    bind_device(int(physical_gpu))
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
            select_devices([int(device) for device in devices])
            return
    physical = isolate_from_argv(argv)
    if physical is not None:
        os.environ['LASTMILE_SAMPLER_GPUS'] = physical
