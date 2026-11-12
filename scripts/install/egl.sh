#!/usr/bin/env bash
# MuJoCo EGL rendering needs the GLVND loader libEGL.so.1. Hosts with only the
# NVIDIA driver (libEGL_nvidia.so.0, e.g. pro5000) lack it: unpack Ubuntu's
# libglvnd packages into the environment (no sudo) and expose them on activate.
set -euo pipefail
venv=$(cd "${1:-$(dirname "$0")/../../.venv}" && pwd)
native=$venv/lib/native
if ! "$venv/bin/python" -c 'import ctypes; ctypes.CDLL("libEGL.so.1")' 2>/dev/null \
    && [[ ! -e $native/libEGL.so.1 ]]; then
    work=$(mktemp -d)
    (cd "$work" && apt-get download libglvnd0 libegl1 libgles2 libgl1 libglx0 libopengl0)
    for deb in "$work"/*.deb; do dpkg-deb -x "$deb" "$work/root"; done
    mkdir -p "$native"
    cp -a "$work"/root/usr/lib/x86_64-linux-gnu/lib*.so* "$native"/
    rm -rf "$work"
fi
if [[ -e $native/libEGL.so.1 ]] && ! grep -q 'lib/native' "$venv/bin/activate"; then
    cat >> "$venv/bin/activate" <<'ACTIVATE'
# Bundled GLVND loader for MuJoCo EGL (scripts/install/egl.sh).
export LD_LIBRARY_PATH="$VIRTUAL_ENV/lib/native${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
ACTIVATE
fi
