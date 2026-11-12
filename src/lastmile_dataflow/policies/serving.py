"""Shared skeleton of the private policy service processes (MolmoBot and π0.5)."""

import argparse
import ctypes
import logging
from multiprocessing.connection import Listener
import os
import signal
import time
import traceback
from typing import Protocol

logger = logging.getLogger(__name__)
PR_SET_PDEATHSIG = 1


class InvalidModelChunk(ValueError):
    """The model returned a chunk of the wrong shape or with non-finite values."""


class PolicyRunner(Protocol):
    def load(self) -> dict:
        """Load the model; returns the extra fields of the ready message."""

    def reset(self, seed: int) -> None: ...

    def infer(self, request: dict) -> dict: ...


def serve(runner_type, *, load_before_accept):
    """Run one service: ``runner_type(config_path)`` is cheap, ``load()`` loads the model.

    MolmoBot loads after the client connects, π0.5 before (startup compile); each keeps its order
    because the client's load timeout covers both.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    # Worker termination must not leak a GPU model process.
    ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    if os.getppid() == 1:
        return
    runner = runner_type(args.config)
    ready = _load(runner) if load_before_accept else None
    with Listener(args.socket, family="AF_UNIX", authkey=bytes.fromhex(os.environ["LASTMILE_POLICY_AUTH"])) as listener:
        os.chmod(args.socket, 0o600)
        with listener.accept() as connection:
            connection.send(ready or _load(runner))
            _answer(connection, runner)


def _load(runner):
    started = time.monotonic()
    info = runner.load()
    return {"ready": True, "load_s": time.monotonic() - started, **info}


def _answer(connection, runner):
    active = None
    try:
        while True:
            request = connection.recv()
            try:
                if active != request["attempt"]:
                    runner.reset(request["seed"])
                    active = request["attempt"]
                connection.send(runner.infer(request))
            except Exception as exc:
                # Reply to every request so the RPC stream stays synchronized; the client classifies the error.
                logger.exception("Policy request failed for %s", request.get("attempt"))
                connection.send(
                    {
                        "error": str(exc),
                        "type": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                        "invalid_action": isinstance(exc, InvalidModelChunk),
                    }
                )
    except (EOFError, BrokenPipeError, ConnectionResetError):
        pass
