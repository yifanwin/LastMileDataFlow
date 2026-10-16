"""Private local IPC model process. Uses the repository-local multitask inference backend."""
import argparse
import logging
import os
import signal
import time
import traceback
from multiprocessing.connection import Listener

import numpy as np

from ..utils.logging_config import setup_logging
from .molmobot_config import load_policy_config


logger = logging.getLogger(__name__)


class TraceAgent:
    def __init__(self, agent):
        self.agent = agent
        self.raw = None
        self.images = None
        self.calls = 0
        self.state = None
        self.prompt = None
        self.require_point = False

    def get_action_chunk(self, **kwargs):
        if kwargs['state'].shape != (22,):
            raise ValueError('expected actual 22D robot state')
        self.images = kwargs['images']
        self.state = kwargs['state'].copy()
        self.prompt = kwargs['task_description']
        if self.require_point and '<points coords=' not in self.prompt:
            raise ValueError('open_conditioning_point_outside_fisheye_crop')
        started = time.monotonic()
        logger.debug('Generating model action chunk')
        self.raw = self.agent.get_action_chunk(**kwargs)
        logger.debug('Generated model chunk in %.2f s', time.monotonic() - started)
        if self.raw.shape != (16,20) or not np.isfinite(self.raw).all():
            raise ValueError('invalid_model_action_chunk')
        self.calls += 1
        return self.raw


def main():
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--socket', required=True)
    args = parser.parse_args()
    # Worker termination must not leak a GPU model process.
    import ctypes
    ctypes.CDLL(None).prctl(1, signal.SIGTERM)
    if os.getppid() == 1: return
    config = load_policy_config(args.config)
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA unavailable; do not silently run a CPU VLA')
    from .multitask.inference import SynthManipMolmoInferenceWrapper
    from .multitask.policy import MolmoBotRBY1MultitaskPolicy, MolmoBotRBY1PickPnPPolicyConfig, MolmoBotRBY1DoorPlusOpenPolicyConfig
    listener = Listener(args.socket, family='AF_UNIX', authkey=bytes.fromhex(os.environ['LASTMILE_POLICY_AUTH']))
    os.chmod(args.socket, 0o600)
    connection = listener.accept()
    try:
        started = time.monotonic()
        logger.info('Loading selected checkpoint %s', config.checkpoint, extra={'progress': True})
        trace = TraceAgent(SynthManipMolmoInferenceWrapper(checkpoint_path=config.checkpoint, tokenizer_path=config.tokenizer_path))
        policies = {}
        for operation, cls in [('pick',MolmoBotRBY1PickPnPPolicyConfig),('open',MolmoBotRBY1DoorPlusOpenPolicyConfig)]:
            values = {}
            if config.gripper_mode != 'model_default':
                values['clamp_gripper'] = config.gripper_mode == 'binary'
            pc = cls(checkpoint_path=config.checkpoint, img_resolution=(1024,576), **values)
            policies[operation] = MolmoBotRBY1MultitaskPolicy(pc)
            policies[operation].agent = trace
        connection.send({'ready':True, 'load_s':time.monotonic()-started,
                         'device':torch.cuda.get_device_name(), 'cuda_peak_bytes':torch.cuda.max_memory_allocated()})
        logger.info('Model ready, allocated %.1f MiB', torch.cuda.memory_allocated()/1024**2)
        active = None
        while True:
            request = connection.recv()
            try:
                operation = request['operation']; attempt = request['attempt']
                if active != attempt:
                    seed = request['seed']
                    import random
                    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
                    for policy in policies.values(): policy.reset()
                    active = attempt
                policy = policies[operation]
                trace.require_point = policy.use_point_prompts
                calls = trace.calls; started = time.monotonic()
                action = policy.inference_model(request['observation'])
                index = policy.buffer_index-1
                vector = np.concatenate([action[group] for group in policy.config.action_move_group_names])
                connection.send({'action':vector, 'raw_action':trace.raw[index].copy(),
                    'inference_s':time.monotonic()-started, 'new_chunk':trace.calls != calls,
                    'chunk_index':index, 'chunk_serial':trace.calls,
                    'gripper_binary':policy.clamp_gripper,
                    'processed_images':trace.images if request.get('first') else None,
                    'conditioning_points':policy._conditioning_points if request.get('first') else None,
                    'model_chunk_state':trace.state.copy(), 'model_prompt':trace.prompt,
                    'cuda_peak_bytes':torch.cuda.max_memory_allocated()})
            except Exception as exc:
                logger.exception('Policy request failed for %s', request.get('attempt'))
                if str(exc)=='invalid_model_action_chunk':
                    logger.error('Rejected original model chunk: %r', trace.raw)
                connection.send({'error':str(exc), 'type':type(exc).__name__, 'traceback':traceback.format_exc(),
                                 'invalid_action':str(exc)=='invalid_model_action_chunk'})
    except (EOFError, BrokenPipeError, ConnectionResetError):
        pass
    finally:
        connection.close(); listener.close()


if __name__ == '__main__':
    main()
