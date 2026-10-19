"""One JAX π0.5 model, local tokenizer, explicit embodiment statistics."""
import json
import logging
from pathlib import Path
import time

import jax
import jax.numpy as jnp
import numpy as np
import sentencepiece

from .core.models import model, pi0_config
from .core.shared.image_tools import resize_with_pad
from .core.shared.nnx_utils import module_jit


IMAGE_KEYS = ('base_0_rgb', 'left_wrist_0_rgb', 'right_wrist_0_rgb')
logger = logging.getLogger(__name__)


class Pi05Inference:
    def __init__(self, config, *, require_stats=True):
        self.config = config
        self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=config.tokenizer_path)
        path = Path(config.norm_stats) if config.norm_stats else Path(config.checkpoint) / 'assets' / 'norm_stats.json'
        self.stats = None
        if path.is_file():
            data = json.loads(path.read_text())
            self.stats = data.get('norm_stats', data)
            keys = ('mean', 'std') if config.normalization == 'mean_std' else ('q01', 'q99')
            for field in ('state', 'actions'):
                for key in keys:
                    if key not in self.stats[field]:
                        raise ValueError(f'{path}: missing {field}.{key}')
        elif require_stats:
            raise FileNotFoundError(f'π0.5 requires matching {config.embodiment} statistics: {path}')
        logger.info('Restoring π0.5 params: %s',config.checkpoint)
        params = model.restore_params(Path(config.checkpoint) / 'params', dtype=jnp.bfloat16)
        logger.info('Parameters restored; constructing π0.5 graph')
        self.model = pi0_config.Pi0Config(pi05=True).load(params)
        logger.info('π0.5 graph ready')
        self.sample = module_jit(self.model.sample_actions)
        self.reset(0)

    def reset(self, seed):
        self.rng = jax.random.key(seed)

    def normalize(self, value, field, *, inverse=False):
        if self.stats is None:
            raise ValueError('normalization unavailable; only normalized model smoke is supported')
        stats = self.stats[field]
        if self.config.normalization == 'mean_std':
            offset = np.asarray(stats['mean'], np.float32)
            scale = np.asarray(stats['std'], np.float32) + 1e-6
        else:
            low = np.asarray(stats['q01'], np.float32)
            high = np.asarray(stats['q99'], np.float32)
            scale = (high - low + 1e-6) / 2
            offset = low + scale
        size = min(value.shape[-1], len(offset))
        result = value.copy()
        result[..., :size] = value[..., :size] * scale[:size] + offset[:size] if inverse else (value[..., :size] - offset[:size]) / scale[:size]
        return result

    def infer(self, observation, *, normalized=False):
        config = self.config
        state = np.asarray(observation['state'], np.float32)
        if state.shape != (config.state_size,) or not np.isfinite(state).all():
            raise ValueError(f'expected finite {config.state_size}D {config.embodiment} state')
        if not normalized:
            state = self.normalize(state, 'state')
        state = np.pad(state, (0, 32 - len(state)))
        prompt = observation['prompt'].strip().replace('_', ' ').replace('\n', ' ')
        bins = np.digitize(state, np.linspace(-1, 1, 257)[:-1]) - 1
        text = f'Task: {prompt}, State: {" ".join(map(str, bins))};\nAction: '
        tokens = self.tokenizer.encode(text, add_bos=True)[:200]
        mask = np.arange(200) < len(tokens)
        tokens = np.pad(np.asarray(tokens, np.int32), (0, 200-len(tokens)))
        images = {}
        processed = []
        for name in IMAGE_KEYS:
            image = np.asarray(observation['images'][name])
            if image.dtype != np.uint8 or image.ndim != 3 or image.shape[-1] != 3:
                raise ValueError(f'{name} requires HWC uint8 RGB')
            image = np.asarray(resize_with_pad(image, 224, 224))
            processed.append(image)
            images[name] = jnp.asarray(image[None], jnp.float32) / 255 * 2 - 1
        obs = model.Observation(images=images,
            image_masks={name: jnp.ones((1,), dtype=bool) for name in IMAGE_KEYS},
            state=jnp.asarray(state[None]), tokenized_prompt=jnp.asarray(tokens[None]),
            tokenized_prompt_mask=jnp.asarray(mask[None]))
        self.rng, key = jax.random.split(self.rng)
        started = time.monotonic()
        raw = np.asarray(self.sample(key, obs, num_steps=config.sample_steps))[0]
        actions = raw if normalized else self.normalize(raw, 'actions', inverse=True)
        actions = actions[:, :config.action_size]
        if actions.shape != (50, config.action_size) or not np.isfinite(actions).all():
            raise ValueError('invalid π0.5 action chunk')
        return {'actions': actions, 'raw_actions': raw, 'inference_s': time.monotonic()-started,
                'processed_images': processed, 'model_state': state, 'model_prompt': text,
                'normalized_smoke': normalized}
