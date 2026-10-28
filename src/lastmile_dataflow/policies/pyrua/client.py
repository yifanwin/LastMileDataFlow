"""Batch-owned RLDX-1 server and per-episode PyRUA agent runs."""
import logging
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

from ..molmobot import PolicyInfrastructureError
from .config import load_config

logger = logging.getLogger(__name__)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


class RldxServer:
    """RPent's own RoboCasa VLA server (`robots/robocasa/vla_server.py`) in its own process.

    RLDX needs transformers 4.57; its packages lead the child's path so the
    simulator process keeps the project environment. One RPC session serves
    the batch and is reset at the start of every episode.
    """

    def __init__(self, config, log_path):
        self.log = Path(log_path).open('ab', buffering=0)
        port = _free_port()
        self.url = f'http://127.0.0.1:{port}'
        paths = [*config.vla_site_packages, config.rpent_root, config.site_packages]
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(paths), HF_HOME=config.vla_hf_home,
                   HF_HUB_CACHE=str(Path(config.vla_hf_home)/'hub'), HF_HUB_OFFLINE='1',
                   TRANSFORMERS_OFFLINE='1', NO_ALBUMENTATIONS_UPDATE='1', PYTHONDONTWRITEBYTECODE='1')
        env['NO_PROXY'] = env['no_proxy'] = ','.join(filter(None, [env.get('no_proxy', ''), '127.0.0.1,localhost']))
        # CUDA_VISIBLE_DEVICES is inherited from the CLI's --gpu selection.
        self.process = subprocess.Popen(
            [config.vla_python, str(Path(config.rpent_root)/'robots/robocasa/vla_server.py'),
             '--model-path', config.vla_model_path, '--transport', 'http', '--host', '127.0.0.1',
             '--port', str(port), '--parent-watch'],
            cwd=config.rpent_root, env=env, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT)
        try:
            from rpent.utils.rpc.client_utils import wait_for_ready
            from rpent.utils.rpc.http_rpc import HttpRpcClient
            from robots.robocasa.vla_client import RoboCasaVLAClient
            self.rpc = HttpRpcClient(self.url, enable_sessions=True)
            deadline = time.monotonic()+config.vla_load_timeout_s
            while True:
                if self.process.poll() is not None:
                    raise PolicyInfrastructureError(f'RLDX server exited; see {log_path}')
                try:
                    wait_for_ready(self.rpc, timeout_s=5.)
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise PolicyInfrastructureError('RLDX server startup timeout')
            self.client = RoboCasaVLAClient(self.rpc)
            self.modality = self.client.get_modality_config()
        except BaseException:
            self.close()
            raise

    def alive(self):
        return self.process.poll() is None

    def close(self):
        if getattr(self, 'rpc', None) is not None:
            self.rpc.close()
            self.rpc = None
        if self.process.poll() is None:
            if self.process.stdin is not None:
                self.process.stdin.close()   # --parent-watch: EOF ends the server
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait()
        self.log.close()


class PyruaPolicy:
    """Episode-level code agent; never asked for single-step actions."""

    policy_name = 'pyrua'

    def __init__(self, path, log_path):
        self.config = load_config(path)
        self.config.import_paths()
        # RPent reads the RLDX evaluation settings from the process environment.
        os.environ.update(RLDX_MAX_CHUNKS=str(self.config.rldx_max_chunks),
                          RLDX_ACTION_STEPS_PER_CHUNK=str(self.config.rldx_action_steps_per_chunk),
                          RLDX_SETTLE_PATIENCE=str(self.config.rldx_settle_patience))
        os.environ.pop('RLDX_RESET_SEED', None)
        os.environ.pop('RLDX_VIDEO_DIR', None)
        self.vla = None
        if self.config.primitives == 'full':
            logger.info('启动 RLDX-1 服务 权重=%s 日志=%s', self.config.vla_model_path, log_path,
                        extra={'progress': True})
            self.vla = RldxServer(self.config, log_path)
        self.metadata = {'ready': True, 'agent_runtime': self.config.runtime, 'model': self.config.model,
                         'vla': None if self.vla is None else {'server': 'rpent robots/robocasa/vla_server.py',
                                                              'model_path': self.config.vla_model_path,
                                                              'modality': self.vla.modality}}

    def run(self, environment, out_dir, *, seed):
        """Run PyRUA's `play_episode` on a worker thread; this thread serves the simulator."""
        from .host import play
        if self.vla is not None:
            self.vla.client.reset_session()
        box = {}

        def target():
            try:
                box['value'] = play(self, environment, Path(out_dir).resolve(), seed=seed)   # play chdirs
            except BaseException as exc:
                box['error'] = exc

        worker = threading.Thread(target=target, name='pyrua-agent', daemon=True)
        worker.start()
        environment.owner.serve(worker)
        worker.join()
        if 'error' in box:
            raise box['error']
        result = box['value']
        if self.vla is not None and not self.vla.alive():
            result['shared_failure'] = 'rldx_server_exited'
        return result

    def close(self):
        if self.vla is not None:
            self.vla.close()
            self.vla = None
