"""One model subprocess per scene worker, exclusively owned and explicitly closed."""
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from multiprocessing.connection import Client

from ..robots.action import InvalidAction
from .molmobot_config import load_policy_config


class PolicyInfrastructureError(RuntimeError):
    pass


class MolmoBotClient:
    def __init__(self, path, log_path):
        self.config = load_policy_config(path)
        self.directory = tempfile.TemporaryDirectory(prefix='lm-vla-')
        socket = str(Path(self.directory.name)/'policy.sock')
        token = secrets.token_hex(32)
        env = dict(os.environ, LASTMILE_POLICY_AUTH=token,
                   PYTHONDONTWRITEBYTECODE='1', TOKENIZERS_PARALLELISM='false')
        package_root = str(Path(__file__).resolve().parents[2])
        env['PYTHONPATH'] = self.config.source_dir + os.pathsep + package_root
        # No caches or downloaded tokenizer assets go into the read-only upstream tree.
        cache = Path(log_path).parent.parent/'dependencies'/'molmobot-cache'
        cache.mkdir(parents=True, exist_ok=True)
        env.update(HF_HOME=str(cache/'hf'), XDG_CACHE_HOME=str(cache), TORCH_HOME=str(cache/'torch'))
        self.log = Path(log_path).open('ab', buffering=0)
        self.connection = None
        self.process = subprocess.Popen([self.config.python, '-m',
            'lastmile_dataflow.policies.molmobot_service', '--config', str(path), '--socket', socket],
            env=env, cwd=package_root, stdout=self.log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+self.config.load_timeout_s
            while not Path(socket).exists():
                if self.process.poll() is not None:
                    raise PolicyInfrastructureError('model process exited; see '+str(log_path))
                if time.monotonic() > deadline: raise PolicyInfrastructureError('model startup timeout')
                time.sleep(.2)
            self.connection = Client(socket, family='AF_UNIX', authkey=bytes.fromhex(token))
            self.metadata = self.receive(max(.1, deadline-time.monotonic()))
            if not self.metadata.get('ready'): raise PolicyInfrastructureError('model not ready')
        except BaseException:
            self.close(); raise

    def receive(self, timeout):
        if not self.connection.poll(timeout):
            raise PolicyInfrastructureError('model response timeout')
        try: response = self.connection.recv()
        except (EOFError, OSError) as exc: raise PolicyInfrastructureError('model connection lost') from exc
        if 'error' in response:
            if response.get('invalid_action'): raise InvalidAction(response['error'])
            raise PolicyInfrastructureError(response['type']+':'+response['error'])
        return response

    def action(self, observation, *, task, attempt, seed, first=False, timeout=None):
        try:
            self.connection.send({'operation':task['operation'], 'attempt':attempt, 'seed':seed,
                                  'observation':observation, 'first':first})
        except (OSError, EOFError) as exc:
            raise PolicyInfrastructureError('model connection lost') from exc
        return self.receive(min(timeout or self.config.service_timeout_s, self.config.service_timeout_s))

    def close(self):
        if self.connection is not None:
            try: self.connection.send({'close':True})
            except (OSError, EOFError): pass
            self.connection.close(); self.connection = None
        if getattr(self, 'process', None) is not None:
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try: self.process.wait(timeout=5)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
        self.log.close(); self.directory.cleanup()
