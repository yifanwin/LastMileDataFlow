"""Global budgets, CPU ownership, subset scoring and bounded process transport."""

import importlib.util
import multiprocessing as mp
import pickle
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import torch

from tests.policies.test_sac import observation
from lastmile_dataflow.station_map.rl.sac.replay import Transition

if importlib.util.find_spec("torch_geometric"):
    from lastmile_dataflow.station_map.rl.training import Samplers, target_updates


def producer(channel, queue, stop):
    for i in range(5):
        queue.put((pickle.dumps((0, i, True, i, "")), 0.0))
    command, _ = channel.recv()
    if command == "pause":
        channel.send("paused")
    stop.wait()


@unittest.skipUnless(importlib.util.find_spec("torch_geometric"), "requires RL dependencies")
class SamplingTests(unittest.TestCase):
    def test_global_credits_keep_remainder_and_skip_warmup(self):
        self.assertEqual(
            [target_updates(h, 20000, 128) for h in (19999, 20000, 20127, 20128, 100000, 200000)],
            [0, 0, 0, 1, 625, 1406],
        )
        healthy = 0
        for count in (13000, 7001, 63, 64):
            healthy += count
        self.assertEqual(target_updates(healthy, 20000, 128), 1)

    def test_cpu_payload_owns_terminal_state_and_tail_selection(self):
        obs = observation(4)
        batch = Transition(
            obs,
            torch.zeros(4, 7),
            torch.arange(4.0),
            obs.clone(),
            torch.ones(4, dtype=torch.bool),
            torch.zeros(4, dtype=torch.bool),
        )
        cpu = batch.to("cpu")
        obs.nodes.zero_()
        self.assertTrue(cpu.obs.nodes[:, 2, 0].all())
        selected = cpu[torch.tensor([3, 1])]
        self.assertEqual(selected.reward.tolist(), [3.0, 1.0])
        self.assertTrue(selected.terminated.all())

    def test_pause_drains_full_queue_and_close_joins(self):
        ctx = mp.get_context("spawn")
        workers = Samplers.__new__(Samplers)
        workers.stop = ctx.Event()
        workers.queue = ctx.Queue(maxsize=1)
        parent, child = ctx.Pipe()
        process = ctx.Process(target=producer, args=(child, workers.queue, workers.stop))
        workers.channels, workers.processes, workers.actors = [parent], [process], []
        process.start()
        child.close()
        try:
            pending = workers.pause()
            self.assertEqual([p[3] for p in pending], list(range(5)))
        finally:
            workers.close()
        self.assertEqual(process.exitcode, 0)

    def test_actor_mailbox_retains_latest_complete_snapshot(self):
        ctx = mp.get_context("spawn")
        workers = Samplers.__new__(Samplers)
        queue = ctx.Queue(maxsize=1)
        workers.actors = [queue]
        agent = SimpleNamespace(actor=torch.nn.Linear(2, 1), updates=0)
        workers.publish(agent, True)
        agent.updates = 16
        with torch.no_grad():
            agent.actor.weight.fill_(7.0)
        workers.publish(agent, False)
        version, random, state = pickle.loads(queue.get(timeout=2))
        self.assertEqual(version, 16)
        self.assertFalse(random)
        self.assertTrue((state["weight"] == 7).all())
        queue.close()

    def test_periodic_scores_only_explicit_subset(self):
        from lastmile_dataflow.station_map.rl.evaluation import station_scores

        env = SimpleNamespace(
            num_envs=2, task=SimpleNamespace(stations=[{"station_id": str(i), "valid": True} for i in range(10)])
        )
        with (
            patch("lastmile_dataflow.station_map.rl.evaluation.reset_stations") as reset,
            patch(
                "lastmile_dataflow.station_map.rl.evaluation.action_sampled_q",
                return_value=(torch.ones(2), torch.zeros(2)),
            ),
        ):
            rows = station_scores(None, env, 16, [3, 8])
        self.assertEqual([r["station_id"] for r in rows], ["3", "8"])
        reset.assert_called_once_with(env, [3, 8])
