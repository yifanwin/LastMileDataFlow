"""Pin a research process to one GPU; call before torch, warp or mjlab are imported."""

import os
import ctypes
import json
import subprocess
import sys


def _query_egl_devices():
    """Query NVIDIA's CUDA ordinals without creating a display or GPU context."""
    egl = ctypes.CDLL("libEGL.so.1")
    egl.eglGetProcAddress.restype = ctypes.c_void_p
    query_devices = ctypes.CFUNCTYPE(
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int)
    )(egl.eglGetProcAddress(b"eglQueryDevicesEXT"))
    query_attribute = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_ssize_t))(
        egl.eglGetProcAddress(b"eglQueryDeviceAttribEXT")
    )
    count = ctypes.c_int()
    if not query_devices(0, None, ctypes.byref(count)):
        raise RuntimeError("cannot enumerate EGL devices; provide --egl-devices explicitly")
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
    environment = dict(os.environ, CUDA_DEVICE_ORDER="PCI_BUS_ID")
    environment.pop("CUDA_VISIBLE_DEVICES", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from lastmile_dataflow.utils.gpu import "
            "_query_egl_devices; print(json.dumps(_query_egl_devices()))",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return {int(gpu): device for gpu, device in json.loads(result.stdout).items()}


def resolve_egl_devices(gpu_ids, egl_devices=None):
    if egl_devices is not None:
        if len(egl_devices) != len(gpu_ids):
            raise ValueError("provide one EGL device index per GPU")
        return list(egl_devices)
    if os.environ.get("MUJOCO_GL") == "disable":
        return [None] * len(gpu_ids)
    mapping = json.loads(os.environ.get("LASTMILE_EGL_DEVICES", "{}"))
    if any(str(gpu) not in mapping for gpu in gpu_ids):
        mapping.update({str(gpu): device for gpu, device in egl_device_map().items()})
    return [mapping[str(gpu)] for gpu in gpu_ids]


def bind_device(gpu, egl_device=None):
    """Bind a worker before its first renderer; CPU workers may then hide CUDA."""
    device = resolve_egl_devices([gpu], None if egl_device is None else [egl_device])[0]
    os.environ.update(CUDA_DEVICE_ORDER="PCI_BUS_ID", CUDA_VISIBLE_DEVICES=str(gpu), LASTMILE_RENDER_GPU=str(gpu))
    if device is not None:
        os.environ["MUJOCO_EGL_DEVICE_ID"] = str(device)
    os.environ.setdefault("MUJOCO_GL", "egl")


def select_devices(gpu_ids, egl_devices=None):
    """Resolve once in the parent and inherit the mapping in every spawned worker."""
    devices = resolve_egl_devices(gpu_ids, egl_devices)
    os.environ["LASTMILE_EGL_DEVICES"] = json.dumps(dict(zip(map(str, gpu_ids), devices)))
    bind_device(gpu_ids[0], devices[0])
    return devices


def bind_cpu_renderer():
    """CPU preparation/execution still render on the parent's selected physical GPU."""
    gpu = os.environ.get("LASTMILE_RENDER_GPU")
    if gpu is not None:
        bind_device(int(gpu))
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
