"""Fixed crop retains complete supports, articulated objects and measured state."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.loading.local import task_local_model
from tests.helpers import table_scene


class LocalModelTests(unittest.TestCase):
    def test_crop_keeps_complete_support_and_articulation_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source, robot = table_scene(directory)
            path = Path(source.xml_path)
            path.write_text(path.read_text().replace('</worldbody>', '''
                <body name="far_object" pos="20 0 .2"><freejoint/><geom type="box" size=".1 .1 .1" mass="1"/></body>
                <body name="door" pos="20 1 .5"><joint name="door_hinge" type="hinge" range="0 1.5"/>
                  <geom type="box" size=".5 .04 .5" mass="1"/>
                  <body name="handle" pos=".4 0 0"><geom type="sphere" size=".04"/></body>
                </body></worldbody>'''))
            sim = Simulation.from_source(source, robot, target='target')
            self.addCleanup(sim.close)
            task = {'target_body': 'target', 'anchor_world': [2, 0, .5]}
            before = sim.state_vector().copy()
            local = task_local_model(sim, task, .8)
            self.addCleanup(local.close)
            self.assertLess(local.model.nbody, sim.model.nbody)
            self.assertEqual(local.local_model['removed_roots'], ['far_object'])
            for name in ('target', 'table', 'neighbor', 'door', 'handle', 'robot_0/base'):
                self.assertGreater(local.model.body(name).id, 0)
            np.testing.assert_array_equal(sim.state_vector(), before)
            for name in ('target_free', 'neighbor_free', 'door_hinge'):
                a, b = sim.model.joint(name).qposadr[0], local.model.joint(name).qposadr[0]
                self.assertEqual(sim.data.qpos[a], local.data.qpos[b])
            local.freeze(Path(directory) / 'local')
            restored = Simulation.from_snapshot(Path(directory) / 'local', robot, target='target')
            self.addCleanup(restored.close)
            self.assertEqual(restored.local_model['removed_roots'], ['far_object'])
