"""一套实时轨迹与观测；不产生发布包、重复 replay 或审计侧车。"""
from pathlib import Path
import uuid
import imageio.v2 as imageio
import numpy as np

from ..io import append_row, write_json
from ..runtime.episode_guard import recording_operation, track_recorder


class AttemptRecorder:
    def __init__(self, root, config, *, attempt_id=None, strategy=None):
        self.output_root = Path(root)
        self.attempt_id = attempt_id or uuid.uuid4().hex[:12]
        self.path = self.output_root / 'rollouts' / self.attempt_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.config = config
        self.collection = config['collection']
        self.steps = 0
        self.finished = False
        self.sim = None
        self.samples = []
        self.actions = []
        self.requested = []
        self.phases = []
        self.writers = {}
        self.frame_index = []
        self.errors = []
        self.phase = 'initialize'
        self.requested_action = None
        self.result = None
        track_recorder(self)

    def bind_simulation(self, sim, *, width=None, height=None):
        if sim.started:
            raise RuntimeError('bind recorder before execution')
        self.sim = sim
        self.width = width or self.collection['width']
        self.height = height or self.collection['height']
        sim.evidence_recorder = self

    @recording_operation
    def event(self, code, **fields):
        append_row(self.path / 'events.jsonl', {'code': code, **fields})

    def intent(self, value, *, producer, metadata=None):
        self.phase = (metadata or {}).get('phase', 'operation')

    @recording_operation
    def capture(self):
        from ..integrations.molmobot_dataset import state22
        groups = {key: {'position': self.sim.robot.group(key)} for key in self.sim.robot.groups}
        sample = {'time_s': float(self.sim.data.time), 'qpos': self.sim.data.qpos.copy(),
                  'qvel': self.sim.data.qvel.copy(), 'ctrl': self.sim.data.ctrl.copy(),
                  'state22': state22(groups)}
        self.samples.append(sample)
        if self.collection.get('record_images', True):
            if self.collection.get('record_depth', False):
                sensors = self.sim.capture_sensors(self.width, self.height, depth=True)
                frames = {key: value['rgb'] for key, value in sensors.items()}
                for key, value in sensors.items():
                    if value['depth'] is not None:
                        target = self.path / 'images' / key
                        target.mkdir(parents=True, exist_ok=True)
                        np.save(target / f'{self.steps:06d}-depth.npy', value['depth'])
            else:
                frames = self.sim.render(self.width, self.height)
            self.sim._raw_frame_cache = (sample['time_s'], self.width, self.height, frames)
            self.observation(frames, step=self.steps, time_s=sample['time_s'])

    @recording_operation
    def executed(self, command):
        self.actions.append(np.asarray(command['submitted_action'], float))
        self.requested.append(np.asarray(self.requested_action, float))
        self.phases.append(self.phase)
        self.steps += 1
        self.capture()

    def record_step(self, *, raw_action, command, before, after, check, observations):
        # State/action are recorded in Simulation at actual interval boundaries.
        # Keep only the execution phase here, not another copy of the trajectory.
        if self.phases:
            self.phases[-1] = observations.get('phase', self.phase)

    @recording_operation
    def observation(self, frames, *, step, time_s):
        if not self.collection.get('record_images', True):
            return {}
        refs = {}
        for camera, pixels in frames.items():
            path = self.path / 'images' / camera / f'{step:06d}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                imageio.imwrite(path, pixels)
            refs[camera] = str(path.relative_to(self.path))
        return refs

    @recording_operation
    def video_frame(self, frames, time_s):
        if not self.collection.get('record_video', False):
            return
        for camera, pixels in frames.items():
            if camera not in self.writers:
                directory = self.path / 'videos'
                directory.mkdir(exist_ok=True)
                self.writers[camera] = imageio.get_writer(directory / f'{camera}.mp4',
                    fps=self.sim.robot.config.control_hz, codec='libx264', macro_block_size=2)
            self.writers[camera].append_data(pixels)

    @recording_operation
    def finish(self, status, reason, results, *, extra=None):
        if self.finished:
            raise RuntimeError('rollout already finished')
        self.abort_close()
        if self.samples:
            arrays = {key: np.asarray([sample[key] for sample in self.samples]) for key in self.samples[0]}
            arrays.update(actions=np.asarray(self.actions).reshape(-1, 20),
                          requested_actions=np.asarray(self.requested).reshape(-1, 20),
                          phases=np.asarray(self.phases, dtype=str))
            np.savez_compressed(self.path / 'trajectory.npz', **arrays)
        complete = not self.errors and len(self.samples) == self.steps + 1
        if self.errors:
            status = 'infrastructure_error' if any(e.get('failure_scope') == 'shared' for e in self.errors) else 'recording_error'
        self.result = {'status': status, 'reason': reason, 'recording_complete': complete,
                       'executed_steps': self.steps, 'recording_errors': self.errors}
        write_json(self.path / 'recording.json', {'frames': len(self.samples), 'steps': self.steps,
            'complete': complete, 'errors': self.errors, 'controller_profile': self.sim.robot.config.control_profile if self.sim else None,
            'task': self.config.get('task'), 'seed': self.config.get('seed')})
        self.finished = True
        return self.path

    def abort_close(self):
        for camera, writer in list(self.writers.items()):
            try:
                writer.close()
            except Exception as exc:
                from ..runtime.episode_guard import classify_episode_exception
                self.errors.append({'camera': camera, **classify_episode_exception(exc, 'recording')})
            del self.writers[camera]


class NullTrace:
    def write(self, value):
        return len(value)

    def flush(self):
        pass

    def close(self):
        pass


def open_trace(path, enabled):
    if not enabled:
        return NullTrace()
    import gzip
    return gzip.open(path, 'wb')
