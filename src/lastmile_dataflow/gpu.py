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
