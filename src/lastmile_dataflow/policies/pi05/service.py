"""Project-owned π0.5 process; inference only, no simulation dependencies."""
import argparse
import logging
from multiprocessing.connection import Listener
import os
import signal
import traceback
import time

from .config import load_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--socket', required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    import ctypes
    ctypes.CDLL(None).prctl(1, signal.SIGTERM)
    if os.getppid() == 1:
        return
    import jax
    if jax.devices()[0].platform != 'gpu':
        raise RuntimeError('π0.5 service requires a visible GPU')
    from .inference import Pi05Inference
    started = time.monotonic()
    runner = Pi05Inference(load_config(args.config))
    # Compile once during startup, covered by the model load timeout.
    import numpy as np
    from .inference import IMAGE_KEYS
    runner.infer({'state': np.zeros(runner.config.state_size, np.float32),
                  'images': {key: np.zeros((224, 224, 3), np.uint8) for key in IMAGE_KEYS},
                  'prompt': 'Pick up the cup.'})
    with Listener(args.socket, family='AF_UNIX', authkey=bytes.fromhex(os.environ['LASTMILE_POLICY_AUTH'])) as listener:
        with listener.accept() as connection:
            connection.send({'ready': True, 'load_s': time.monotonic()-started,
                             'device': str(jax.devices()[0]), 'embodiment': runner.config.embodiment,
                             'normalization': runner.config.normalization})
            active = None
            try:
                while True:
                    request = connection.recv()
                    try:
                        if active != request['attempt']:
                            runner.reset(request['seed'])
                            active = request['attempt']
                        connection.send(runner.infer(request['observation']))
                    except Exception as exc:
                        logging.exception('π0.5 inference failed')
                        connection.send({'error': str(exc), 'type': type(exc).__name__, 'traceback': traceback.format_exc()})
            except EOFError:
                pass


if __name__ == '__main__':
    main()
