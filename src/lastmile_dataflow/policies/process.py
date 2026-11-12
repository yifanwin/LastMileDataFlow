"""One policy subprocess per scene worker, exclusively owned and explicitly closed."""
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from multiprocessing.connection import Client

import numpy as np

from ..runtime.control.action import InvalidAction
from ..runtime.execution.lifecycle import classify_episode_exception

logger = logging.getLogger(__name__)
SHARED_ERROR_TYPES = ('MemoryError', 'OutOfMemoryError')


class PolicyInfrastructureError(RuntimeError):
    shared_resource = True


class PolicyEpisodeError(RuntimeError):
    """Consumed response to one bad request; RPC stream remains synchronized."""


@dataclass
class PolicyAction:
    """One control step from a policy client, independent of the backend."""
    action: np.ndarray
    raw_action: np.ndarray
    new_chunk: bool
    chunk_index: int
    chunk_serial: int
    inference_s: float
    model_prompt: str
    model_chunk_state: np.ndarray
    processed_images: list | None = None
    conditioning_points: dict | None = None
    gripper_binary: bool | None = None


class PolicyProcess:
    """Authenticated local RPC to ``python -m <service_module>``; startup waits at most ``load_timeout_s``."""

    def __init__(self, config, config_path, log_path, *, service_module, name, drop_env=()):
        logger.info('启动 %s 配置=%s 子进程日志=%s', name, config_path, log_path, extra={'progress': True})
        self.config = config
        self.directory = tempfile.TemporaryDirectory(prefix='lm-vla-')
        socket = str(Path(self.directory.name) / 'policy.sock')
        token = secrets.token_hex(32)
        env = dict(os.environ, LASTMILE_POLICY_AUTH=token,
                   PYTHONDONTWRITEBYTECODE='1', TOKENIZERS_PARALLELISM='false', XLA_PYTHON_CLIENT_PREALLOCATE='false')
        # Keep runtime caches with this experiment, not beside model weights.
        cache = Path(log_path).parent.parent / 'dependencies' / (name.lower() + '-cache')
        cache.mkdir(parents=True, exist_ok=True)
        env.update(HF_HOME=str(cache / 'hf'), XDG_CACHE_HOME=str(cache), TORCH_HOME=str(cache / 'torch'))
        for key in drop_env:
            env.pop(key, None)
        self.log = Path(log_path).open('ab', buffering=0)
        self.connection = None
        self.process = subprocess.Popen([config.python, '-m', service_module, '--config', str(config_path),
                                         '--socket', socket], env=env, stdout=self.log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + config.load_timeout_s
            while not Path(socket).exists():
                if self.process.poll() is not None:
                    raise PolicyInfrastructureError('model process exited; see ' + str(log_path))
                if time.monotonic() > deadline:
                    raise PolicyInfrastructureError('model startup timeout')
                time.sleep(.2)
            self.connection = Client(socket, family='AF_UNIX', authkey=bytes.fromhex(token))
            self.metadata = self.receive(max(.1, deadline - time.monotonic()))
            if not self.metadata.get('ready'):
                raise PolicyInfrastructureError('model not ready')
            logger.info('%s 已就绪 metadata=%s', name, self.metadata)
        except BaseException:
            self.close()
            raise

    def receive(self, timeout):
        try:
            if not self.connection.poll(timeout):
                self.close()
                raise PolicyInfrastructureError('model response timeout')
            response = self.connection.recv()
        except (EOFError, OSError) as exc:
            raise PolicyInfrastructureError('model connection lost') from exc
        if not isinstance(response, dict):
            raise PolicyInfrastructureError('malformed model response')
        if 'error' in response:
            if response.get('invalid_action'):
                raise InvalidAction(response['error'])
            message = response['type'] + ':' + response['error']
            if (response['type'] not in SHARED_ERROR_TYPES
                    and classify_episode_exception(RuntimeError(message))['failure_scope'] == 'episode'):
                raise PolicyEpisodeError(message)
            raise PolicyInfrastructureError(message)
        return response

    def predict(self, observation, *, operation, attempt, seed, first):
        """One round-trip with the fixed ``rpc_timeout_s``; the caller owns the attempt budget."""
        try:
            self.connection.send({'operation': operation, 'attempt': attempt, 'seed': seed,
                                  'observation': observation, 'first': first})
        except (OSError, EOFError) as exc:
            raise PolicyInfrastructureError('model connection lost') from exc
        return self.receive(self.config.rpc_timeout_s)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()
        self.directory.cleanup()
