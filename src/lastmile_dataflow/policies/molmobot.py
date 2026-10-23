"""One model subprocess per scene worker, exclusively owned and explicitly closed."""
import logging
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from multiprocessing.connection import Client

from ..runtime.control.action import InvalidAction
from .molmobot_config import load_policy_config

logger = logging.getLogger(__name__)


class PolicyInfrastructureError(RuntimeError):
    shared_resource = True


class PolicyEpisodeError(RuntimeError):
    """Consumed response to one bad request; RPC stream remains synchronized."""
    pass


class MolmoBotClient:
    load_config = staticmethod(load_policy_config)
    service_module = 'lastmile_dataflow.policies.molmobot_service'
    policy_name = 'MolmoBot'

    def prepare_environment(self, env):
        return env

    def __init__(self, path, log_path):
        logger.info('启动 %s 配置=%s 子进程日志=%s', self.policy_name, path, log_path, extra={'progress': True})
        self.config = self.load_config(path)
        self.directory = tempfile.TemporaryDirectory(prefix='lm-vla-')
        socket = str(Path(self.directory.name)/'policy.sock')
        token = secrets.token_hex(32)
        env = dict(os.environ, LASTMILE_POLICY_AUTH=token,
                   PYTHONDONTWRITEBYTECODE='1', TOKENIZERS_PARALLELISM='false', XLA_PYTHON_CLIENT_PREALLOCATE='false')
        # Keep runtime caches with this experiment, not beside model weights.
        cache = Path(log_path).parent.parent/'dependencies'/(self.policy_name.lower()+'-cache')
        cache.mkdir(parents=True, exist_ok=True)
        env.update(HF_HOME=str(cache/'hf'), XDG_CACHE_HOME=str(cache), TORCH_HOME=str(cache/'torch'))
        env = self.prepare_environment(env)
        self.log = Path(log_path).open('ab', buffering=0)
        self.connection = None
        self.process = subprocess.Popen([self.config.python, '-m',
            self.service_module, '--config', str(path), '--socket', socket],
            env=env, stdout=self.log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+self.config.load_timeout_s
            while not Path(socket).exists():
                if self.process.poll() is not None:
                    raise PolicyInfrastructureError('model process exited; see '+str(log_path))
                if time.monotonic() > deadline:
                    raise PolicyInfrastructureError('model startup timeout')
                time.sleep(.2)
            self.connection = Client(socket, family='AF_UNIX', authkey=bytes.fromhex(token))
            self.metadata = self.receive(max(.1, deadline-time.monotonic()))
            if not self.metadata.get('ready'):
                raise PolicyInfrastructureError('model not ready')
            logger.info('%s 已就绪 metadata=%s', self.policy_name, self.metadata)
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
        if not isinstance(response,dict):
            raise PolicyInfrastructureError('malformed model response')
        if 'error' in response:
            if response.get('invalid_action'):
                error = InvalidAction(response['error'])
            else:
                message = response['type'] + ':' + response['error']
                from ..runtime.execution.lifecycle import classify_episode_exception
                shared = response['type'] in ('MemoryError', 'OutOfMemoryError') or (
                    classify_episode_exception(RuntimeError(message))['failure_scope'] == 'shared')
                error = PolicyInfrastructureError(message) if shared else PolicyEpisodeError(message)
            if response.get('traceback'):
                error.add_note('Policy process traceback:\n' + response['traceback'])
            raise error
        return response

    def action(self, observation, *, task, attempt, seed, first=False, timeout=None):
        """One prediction round-trip.

        `timeout` defaults to the fixed `rpc_timeout_s`, which must stay decoupled
        from the attempt wall-clock budget: a nearly-exhausted budget used to be
        passed through here, so the last step of a full-horizon attempt always
        raced a sub-second RPC deadline and turned into a bogus shared
        `model response timeout`. The caller owns the attempt budget and re-checks
        it after this returns.
        """
        try:
            self.connection.send({'operation':task['operation'], 'attempt':attempt, 'seed':seed,
                                  'observation':observation, 'first':first})
        except (OSError, EOFError) as exc:
            raise PolicyInfrastructureError('model connection lost') from exc
        return self.receive(timeout or self.config.rpc_timeout_s)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        for stop in (self.process.terminate, self.process.kill):
            try:
                self.process.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                stop()
        self.process.wait()
        self.log.close()
        self.directory.cleanup()
